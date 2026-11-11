"""Install a project-local Neo4j Community service on macOS/Linux."""
import hashlib
import os
from pathlib import Path
import re
import secrets
import subprocess
import tarfile
from tempfile import TemporaryDirectory
import urllib.request
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
VERSION = "5.26.33"
RUNTIME = ROOT / "data/runtime"
NEO4J_INSTALL = RUNTIME / f"neo4j-community-{VERSION}"


def main():
    RUNTIME.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    if os.path.exists("/usr/libexec/java_home"):
        env["JAVA_HOME"] = subprocess.check_output(["/usr/libexec/java_home", "-v", "21"], text=True).strip()
    if not NEO4J_INSTALL.exists():
        dotenv=ROOT/'.env'
        existing=dotenv_values(dotenv) if dotenv.exists() else {}
        if existing.get('MEMORY_NEO4J_URI') not in (None,'','bolt://127.0.0.1:7687'):
            raise RuntimeError('当前配置指向其他 Neo4j；本机安装脚本不会覆盖它')
        name = f"neo4j-community-{VERSION}-unix.tar.gz"
        url = "https://dist.neo4j.org/" + name
        with TemporaryDirectory(prefix="chronicle-neo4j-") as temporary:
            archive = Path(temporary) / name
            print(f"Downloading Neo4j Community {VERSION}…", flush=True)
            urllib.request.urlretrieve(url, archive)
            with urllib.request.urlopen(url + ".sha256", timeout=30) as response:
                expected = re.search(r"[a-fA-F0-9]{64}", response.read().decode()).group().lower()
            with archive.open('rb') as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != expected:
                raise RuntimeError("Neo4j archive SHA-256 mismatch")
            with tarfile.open(archive) as bundle:
                bundle.extractall(RUNTIME, filter="data")
        password = existing.get('MEMORY_NEO4J_PASSWORD') or secrets.token_urlsafe(30)
        namespace = existing.get('MEMORY_NAMESPACE') or 'default'
        subprocess.run([str(NEO4J_INSTALL / "bin/neo4j-admin"), "dbms", "set-initial-password", password],
                       env=env, check=True, stdout=subprocess.DEVNULL)
        with dotenv.open("a") as output:
            output.write(f"\nMEMORY_NEO4J_URI=bolt://127.0.0.1:7687\nMEMORY_NEO4J_USER=neo4j\nMEMORY_NEO4J_PASSWORD={password}\nMEMORY_NEO4J_DATABASE=neo4j\nMEMORY_NAMESPACE={namespace}\n")
        dotenv.chmod(0o600)
        with (NEO4J_INSTALL / "conf/neo4j.conf").open("a") as output:
            output.write("\nserver.default_listen_address=127.0.0.1\nserver.bolt.listen_address=:7687\nserver.http.listen_address=:7474\nserver.memory.heap.initial_size=256m\nserver.memory.heap.max_size=512m\nserver.memory.pagecache.size=256m\n")
    status = subprocess.run([str(NEO4J_INSTALL / "bin/neo4j"), "status"], env=env, capture_output=True)
    if status.returncode:
        subprocess.run([str(NEO4J_INSTALL / "bin/neo4j"), "start"], env=env, check=True)
    print(f"Neo4j home: {NEO4J_INSTALL}\nConnection settings: {ROOT / '.env'}")


if __name__ == "__main__":
    main()
