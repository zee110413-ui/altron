"""llama-server for Linux on GitHub's machines: the newest llama.cpp release that has a Linux processor build, or a
build from the sources if no release has one. Prints the path of llama-server.

    python ci/get_llama.py <folder>
"""
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

UA = {"User-Agent": "altron-ci/1.0"}
if os.environ.get("GH_TOKEN"):
    # 20 machines at once run into the API's limit for anonymous requests
    API = dict(UA, Authorization="Bearer " + os.environ["GH_TOKEN"])
else:
    API = UA


def get(url):
    headers = API if url.startswith("https://api.github.com/") else UA
    for i in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=300) as r:
                return r.read()
        except OSError:
            if i == 4:
                raise
            time.sleep(10 * (i + 1))


def main():
    dest = Path(sys.argv[1]).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    rels = json.loads(get("https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=40"))
    latest = json.loads(get("https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"))
    print("releases/latest: %s (%d files: %s)" % (latest.get("tag_name"), len(latest.get("assets", [])),
                                                   ", ".join(a["name"] for a in latest.get("assets", []))[:300]),
          file=sys.stderr)
    for rel in rels:
        for a in rel.get("assets", []):
            if re.search(r"bin-ubuntu-x64\.(zip|tar\.gz)$", a["name"]):
                print("llama.cpp %s: %s" % (rel["tag_name"], a["name"]), file=sys.stderr)
                data = get(a["browser_download_url"])
                if a["name"].endswith(".zip"):
                    zipfile.ZipFile(io.BytesIO(data)).extractall(dest)
                else:
                    tarfile.open(fileobj=io.BytesIO(data)).extractall(dest)
                srv = next(dest.rglob("llama-server"))
                srv.chmod(0o755)
                print(srv)
                return
    print("no Linux build in the releases: building from the sources", file=sys.stderr)
    src = dest / "src"
    subprocess.run(["git", "clone", "--depth", "1", "https://github.com/ggml-org/llama.cpp", str(src)], check=True)
    subprocess.run(["cmake", "-B", "build", "-DGGML_NATIVE=ON", "-DLLAMA_CURL=OFF", "-DCMAKE_BUILD_TYPE=Release"],
                   cwd=src, check=True, stdout=sys.stderr)
    subprocess.run(["cmake", "--build", "build", "-j", "4", "--target", "llama-server"], cwd=src, check=True,
                   stdout=sys.stderr)
    print(next((src / "build").rglob("llama-server")))


if __name__ == "__main__":
    main()
