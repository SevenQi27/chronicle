package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"math"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/pelletier/go-toml/v2"
)

const version = "0.3.0"

type mcpConfig struct {
	Args    []string `json:"args" toml:"args"`
	CWD     string   `json:"cwd" toml:"cwd"`
	Enabled *bool    `json:"enabled" toml:"enabled"`
}

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err := run(ctx, os.Args[1:], os.Stdin, os.Stdout); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return
		}
		if errors.Is(err, context.Canceled) {
			os.Exit(130)
		}
		value := failure(err)
		fmt.Fprintln(os.Stderr, jsonString(value))
		if stringField(value["error"].(map[string]any), "code") == "invalid_arguments" {
			os.Exit(2)
		}
		os.Exit(4)
	}
}

func usage(out io.Writer) {
	fmt.Fprintln(out, "Chronicle 本地记忆\n\n用法: chronicle [--version] <mcp|ui|capture> [选项]\n"+
		"  mcp      启动 SQLite stdio MCP 服务\n  ui       启动本地只读 3D 图谱\n"+
		"  capture  对话采集、归档和队列状态 (submit, worker, status, setup-codex)")
}

type targetOptions struct {
	path, namespace, queue string
}

func run(ctx context.Context, args []string, in io.Reader, out io.Writer) error {
	if len(args) == 0 {
		usage(out)
		return invalid("请选择 mcp、ui 或 capture")
	}
	if option(args[0], "-h", "--help", "help") {
		usage(out)
		return nil
	}
	if args[0] == "--version" {
		_, err := fmt.Fprintln(out, version)
		return err
	}
	command := args[0]
	args = args[1:]
	action := ""
	if command == "capture" {
		if len(args) == 0 || option(args[0], "-h", "--help") {
			fmt.Fprintln(out, "用法: chronicle capture <submit|worker|status|setup-codex> [选项]")
			if len(args) == 0 {
				return invalid("请选择采集子命令")
			}
			return nil
		}
		action, args = args[0], args[1:]
		if !option(action, "submit", "worker", "status", "setup-codex", "hook-codex") {
			return invalid("未知采集子命令：" + action)
		}
	} else if !option(command, "mcp", "ui") {
		return invalid("未知命令：" + command)
	}
	flags := flag.NewFlagSet(strings.TrimSpace(command+" "+action), flag.ContinueOnError)
	flags.SetOutput(out)
	values := targetOptions{namespace: "default"}
	var port int
	var interval float64
	var input, codexHome string
	var once bool
	if action != "hook-codex" {
		flags.StringVar(&values.path, "sqlite", "", "SQLite 数据库路径")
		flags.StringVar(&values.namespace, "namespace", "default", "记忆分区")
	}
	switch command {
	case "mcp":
		flags.StringVar(&values.queue, "capture-queue", "", "采集队列文件")
	case "ui":
		flags.IntVar(&port, "port", 8765, "图谱监听端口")
	case "capture":
		flags.StringVar(&values.queue, "queue", "", "采集队列文件")
		switch action {
		case "submit":
			flags.StringVar(&input, "input", "", "事件 JSON 文件，默认 stdin")
		case "worker":
			flags.Float64Var(&interval, "interval", 2, "队列检查间隔（秒）")
			flags.BoolVar(&once, "once", false, "处理一批后退出")
		case "setup-codex":
			flags.StringVar(&codexHome, "codex-home", "", "Codex 配置目录")
		}
	}
	if err := flags.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return err
		}
		return invalid(err.Error())
	}
	if flags.NArg() != 0 {
		return invalid("无法识别的参数：" + strings.Join(flags.Args(), " "))
	}
	explicitNamespace := false
	flags.Visit(func(f *flag.Flag) {
		if f.Name == "namespace" {
			explicitNamespace = true
		}
	})
	if command == "ui" || action == "setup-codex" && values.queue == "" {
		var err error
		values, err = withCodexDefaults(ctx, values, explicitNamespace, action == "setup-codex")
		if err != nil {
			return err
		}
	}
	resolved, err := resolveTarget(values)
	if err != nil {
		return err
	}
	switch command {
	case "ui":
		return serveUI(ctx, resolved.path, resolved.namespace, port, out)
	case "mcp":
		return serveMCP(ctx, resolved.path, resolved.namespace, resolved.queue, &mcp.StdioTransport{})
	default:
		if resolved.queue == "" {
			resolved.queue = defaultQueue(resolved.path, resolved.namespace)
		}
		return runCapture(ctx, action, resolved, input, codexHome, interval, once, in, out)
	}
}

func runCapture(ctx context.Context, action string, target targetOptions, input, home string,
	interval float64, once bool, in io.Reader, out io.Writer) error {
	if action == "hook-codex" {
		return codexHook(ctx, target.queue, in, out)
	}
	if action == "setup-codex" {
		if home == "" {
			home = codexDirectory()
		}
		home, err := absolutePath(home)
		if err != nil {
			return err
		}
		executable, err := os.Executable()
		if err != nil {
			return err
		}
		result, err := installCodexHooks(home, target.queue, executable)
		return writeResult(out, result, err)
	}
	queue, err := openQueue(ctx, target.queue)
	if err != nil {
		return err
	}
	defer queue.db.Close()
	switch action {
	case "submit":
		var data []byte
		if input != "" {
			data, err = os.ReadFile(input)
		} else {
			data, err = io.ReadAll(in)
		}
		if err != nil {
			return err
		}
		var event captureEvent
		if err := decodeStrict(data, &event); err != nil {
			return err
		}
		result, err := queue.submit(ctx, event, false)
		return writeResult(out, result, err)
	case "status":
		result, err := queue.status(ctx)
		return writeResult(out, result, err)
	default:
		if math.IsNaN(interval) || math.IsInf(interval, 0) || interval <= 0 || interval >= float64(math.MaxInt64)/float64(time.Second) {
			return invalid("capture interval must be positive")
		}
		return runWorker(ctx, queue, target.path, target.namespace, time.Duration(interval*float64(time.Second)), once, out)
	}
}

func writeResult(out io.Writer, result object, err error) error {
	if err != nil {
		return err
	}
	_, err = fmt.Fprintln(out, jsonString(result))
	return err
}

func expandHome(path string) string {
	if path == "~" || strings.HasPrefix(path, "~/") || strings.HasPrefix(path, `~\`) {
		home, err := os.UserHomeDir()
		if err == nil {
			return filepath.Join(home, strings.TrimLeft(path[1:], `/\`))
		}
	}
	return path
}

func absolutePath(path string) (string, error) {
	path, err := filepath.Abs(expandHome(path))
	if err != nil {
		return "", err
	}
	return resolveSymlinks(path), nil
}

func resolveSymlinks(path string) string {
	if resolved, err := filepath.EvalSymlinks(path); err == nil {
		return resolved
	}
	parent := filepath.Dir(path)
	if parent == path {
		return path
	}
	if _, err := os.Lstat(path); err != nil && !errors.Is(err, os.ErrNotExist) {
		return path
	}
	return filepath.Join(resolveSymlinks(parent), filepath.Base(path))
}

func resolveTarget(values targetOptions) (targetOptions, error) {
	var err error
	if values.path == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return values, err
		}
		values.path = filepath.Join(home, ".local", "share", "chronicle", "memory.sqlite3")
	}
	values.path, err = absolutePath(values.path)
	if err != nil {
		return values, err
	}
	if values.queue != "" {
		values.queue, err = absolutePath(values.queue)
	}
	return values, err
}

func codexDirectory() string {
	if value := os.Getenv("CODEX_HOME"); value != "" {
		return expandHome(value)
	}
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".codex")
}

func loadCodexConfig(ctx context.Context) (mcpConfig, error) {
	timeout, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	if data, err := exec.CommandContext(timeout, "codex", "mcp", "get", "chronicle", "--json").Output(); err == nil {
		entry, err := rawValue[struct {
			Enabled   *bool     `json:"enabled"`
			Transport mcpConfig `json:"transport"`
		}](string(data))
		if err != nil {
			return mcpConfig{}, invalid(err.Error())
		}
		if entry.Enabled != nil && !*entry.Enabled {
			return mcpConfig{}, nil
		}
		return entry.Transport, nil
	}
	if data, err := os.ReadFile(filepath.Join(codexDirectory(), "config.toml")); err == nil {
		var config struct {
			Servers map[string]mcpConfig `toml:"mcp_servers"`
		}
		if err := toml.Unmarshal(data, &config); err != nil {
			return mcpConfig{}, invalid(err.Error())
		}
		if entry, found := config.Servers["chronicle"]; found {
			if entry.Enabled != nil && !*entry.Enabled {
				return mcpConfig{}, nil
			}
			return entry, nil
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return mcpConfig{}, err
	}
	if data, err := os.ReadFile(".mcp.json"); err == nil {
		value, err := rawValue[struct {
			Servers map[string]mcpConfig `json:"mcpServers"`
		}](string(data))
		if err != nil {
			return mcpConfig{}, invalid(err.Error())
		}
		return value.Servers["chronicle"], nil
	} else if !errors.Is(err, os.ErrNotExist) {
		return mcpConfig{}, err
	}
	return mcpConfig{}, nil
}

func configuredTarget(config mcpConfig) (targetOptions, error) {
	target := targetOptions{namespace: "default"}
	for i := 0; i < len(config.Args); i++ {
		name, value, inline := strings.Cut(config.Args[i], "=")
		if !option(name, "--sqlite", "--namespace", "--capture-queue") {
			continue
		}
		if !inline {
			if i+1 >= len(config.Args) {
				return target, invalid("无效的 Chronicle MCP 启动参数：" + name)
			}
			i++
			value = config.Args[i]
		}
		switch name {
		case "--sqlite":
			target.path = configuredPath(config.CWD, value)
		case "--namespace":
			target.namespace = value
		case "--capture-queue":
			target.queue = configuredPath(config.CWD, value)
		}
	}
	return target, nil
}

func configuredPath(cwd, value string) string {
	value = expandHome(value)
	if filepath.IsAbs(value) {
		return value
	}
	return filepath.Join(expandHome(cwd), value)
}

func withCodexDefaults(ctx context.Context, requested targetOptions, explicitNamespace, capture bool) (targetOptions, error) {
	if requested.path != "" && explicitNamespace && !capture {
		return requested, nil
	}
	config, err := loadCodexConfig(ctx)
	if err != nil {
		return requested, err
	}
	configured, err := configuredTarget(config)
	if err != nil {
		return requested, err
	}
	if requested.path == "" {
		requested.path = configured.path
	}
	if !explicitNamespace {
		requested.namespace = configured.namespace
	}
	if capture && configured.queue != "" {
		a, err := resolveTarget(requested)
		if err != nil {
			return requested, err
		}
		b, err := resolveTarget(configured)
		if err != nil {
			return requested, err
		}
		if a.path == b.path && a.namespace == b.namespace {
			requested.queue = b.queue
		}
	}
	return requested, nil
}
