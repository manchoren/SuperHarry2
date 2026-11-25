"""Exercise the disposable skill sandbox on a Docker host."""
import base64
import io
import os
import subprocess
import zipfile
from uuid import uuid4

from scripts.skill_sandbox import container_name, run_skill as execute_skill


test_jobs = []


def run_skill(request, timeout=30):
    job_id = uuid4()
    test_jobs.append(job_id)
    return execute_skill(request, timeout=timeout, job_id=job_id,
                         image=os.environ.get("SKILL_TEST_IMAGE", "superharry-skill-worker:local"))


def fixture_wheel(version="1.0"):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as wheel:
        wheel.writestr("demo_skill_dep.py", "def value(): return 'dependency ready'\n")
        wheel.writestr(f"demo_skill_dep-{version}.dist-info/METADATA", f"Metadata-Version: 2.1\nName: demo-skill-dep\nVersion: {version}\n")
        wheel.writestr(f"demo_skill_dep-{version}.dist-info/WHEEL", "Wheel-Version: 1.0\nGenerator: smoke\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        wheel.writestr(f"demo_skill_dep-{version}.dist-info/RECORD", "")
    return base64.b64encode(archive.getvalue()).decode("ascii")


def main():
    prepared = run_skill({
        "operation": "prepare", "files": {"main.py": "raise RuntimeError('must not run')"},
        "entrypoint": "main.py", "input": "",
        "dependencies": ["demo-skill-dep==1.0"],
        "wheels": {"demo_skill_dep-1.0-py3-none-any.whl": fixture_wheel()},
    })
    assert prepared == {"status": "complete", "output": ""}, prepared
    result = run_skill({
        "files": {"main.py": "print(input().upper())"},
        "entrypoint": "main.py",
        "input": "hello",
    })
    assert result == {"status": "complete", "output": "HELLO\n"}, result
    result = run_skill({
        "files": {"main.py": "from demo_skill_dep import value; print(value())"},
        "entrypoint": "main.py", "input": "", "dependencies": ["demo-skill-dep==1.0"],
        "wheels": {"demo_skill_dep-1.0-py3-none-any.whl": fixture_wheel()},
    })
    assert result == {"status": "complete", "output": "dependency ready\n"}, result
    probe = """import os, socket
assert os.getuid() == 10001
assert not os.path.exists('/run/secrets')
assert not os.path.exists('/var/run/docker.sock')
assert not os.path.exists('/data/db.sqlite3')
assert not os.path.exists('/app')
assert set(os.listdir('/sys/class/net')) == {'lo'}
assert open('/sys/fs/cgroup/cpu.max').read().strip() == '50000 100000'
assert open('/sys/fs/cgroup/pids.max').read().strip() == '32'
assert open('/sys/fs/cgroup/memory.max').read().strip() == '134217728'
try:
    open('/readonly-probe', 'w').write('forbidden')
except OSError:
    pass
else:
    raise RuntimeError('root filesystem writable')
try:
    socket.create_connection(('192.0.2.1', 80), timeout=1)
except OSError:
    pass
else:
    raise RuntimeError('network available')
print('isolated')
"""
    result = run_skill({"files": {"probe.py": probe}, "entrypoint": "probe.py", "input": ""})
    assert result == {"status": "complete", "output": "isolated\n"}, result
    result = run_skill({"files": {"fail.py": "raise ValueError()"}, "entrypoint": "fail.py", "input": ""})
    assert result["status"] == "failed", result
    result = run_skill({"files": {"flood.py": "print('x' * 20000)"}, "entrypoint": "flood.py", "input": ""})
    assert result["status"] == "failed", result
    result = run_skill({
        "files": {"direct.py": "import os; fd = os.open('/proc/1/fd/1', os.O_WRONLY); os.write(fd, b'x' * 200000)"},
        "entrypoint": "direct.py", "input": "",
    }, timeout=8)
    assert result["status"] == "failed", result
    result = run_skill({"files": {"slow.py": "import time; time.sleep(60)"}, "entrypoint": "slow.py", "input": ""}, timeout=3)
    assert result["status"] == "timeout", result
    result = run_skill({
        "files": {"main.py": "print('unused')"}, "entrypoint": "main.py", "input": "",
        "dependencies": ["missing-package==1.0"], "wheels": {},
    })
    assert result["status"] == "failed", result
    assert result.get("stage") == "preparation", result
    assert result.get("reason") == "dependency_preparation_failed", result
    result = run_skill({
        "files": {"main.py": "print('unused')"}, "entrypoint": "main.py", "input": "",
        "dependencies": ["--index-url=https://example.invalid"], "wheels": {},
    })
    assert result.get("status") == "failed" and result.get("reason") == "invalid_request", result
    containers = subprocess.check_output(
        ["docker", "ps", "-a", "--filter", "name=superharry-skill-", "--format", "{{.Names}}"],
        text=True, timeout=15,
    )
    remaining = set(containers.splitlines())
    assert not remaining.intersection(container_name(job_id) for job_id in test_jobs), remaining
    print("Skill sandbox smoke passed.")


if __name__ == "__main__":
    main()
