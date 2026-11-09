"""Static checks of the release pipeline: version, icons, spec, installer script, workflow, code hygiene."""
import ast, os, re, sys
import pytest
import yaml
from conftest import ROOT

P = lambda *a: os.path.join(ROOT, *a)
read = lambda *a: open(P(*a), encoding="utf-8").read()


def test_version_is_semver_and_used_by_workflow_and_spec():
    assert re.fullmatch(r"\d+\.\d+\.\d+", read("VERSION").strip())
    assert "VERSION" in read("serial_monitor.spec") and "VERSION" in read(".github", "workflows", "build-windows.yml")


def test_icon_has_all_windows_sizes_and_logo_assets():
    from PIL import Image
    ico = Image.open(P("assets", "icon.ico"))
    assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= set(ico.info["sizes"])
    assert Image.open(P("assets", "icon.png")).size[0] >= 256 and Image.open(P("assets", "header_logo.png")).size[1] >= 60
    assert 'class="badge"' in read("static", "index.html") and "data:image/png;base64" in read("static", "index.html")


def _stub_namespace():
    class N:
        def __init__(self, *a, **k): self.a, self.k = a, k
    return {n: type(n, (N,), {}) for n in ("VSVersionInfo", "FixedFileInfo", "StringFileInfo", "StringTable", "StringStruct", "VarFileInfo", "VarStruct")}


def test_version_resource_content_any_platform():
    import make_version_info
    vi = eval(make_version_info.build_text("1.2.3"), _stub_namespace())
    assert vi.k["ffi"].k["filevers"] == (1, 2, 3, 0)
    table = vi.k["kids"][0].a[0][0]
    strings = {x.a[0]: x.a[1] for x in table.a[1]}
    assert strings["FileVersion"] == "1.2.3" and strings["OriginalFilename"] == "SerialMonitor.exe" and strings["ProductName"] == "Serial Monitor"


@pytest.mark.skipif(sys.platform != "win32", reason="PyInstaller's version-info loader is Windows-only")
def test_version_resource_parses_with_real_pyinstaller_loader(tmp_path):
    import make_version_info
    from PyInstaller.utils.win32.versioninfo import load_version_info_from_text_file
    f = tmp_path / "v.txt"; make_version_info.write(str(f), "1.2.3")
    vi = load_version_info_from_text_file(str(f))
    ffi = vi.ffi                                   # PyInstaller packs (1,2,3,0) into two 32-bit words
    assert (ffi.fileVersionMS, ffi.fileVersionLS) == ((1 << 16) | 2, (3 << 16) | 0)
    assert (ffi.productVersionMS, ffi.productVersionLS) == ((1 << 16) | 2, (3 << 16) | 0)
    strings = {x.name: x.val for t in vi.kids[0].kids for x in t.kids}
    assert strings["FileVersion"] == "1.2.3" and strings["ProductName"] == "Serial Monitor"


def test_spec_is_valid_python_and_references_existing_files():
    src = read("serial_monitor.spec"); ast.parse(src)
    for rel in re.findall(r'\("([^"]+)", "', src): assert os.path.exists(P(rel)), rel
    assert "upx=False" in src and "console=False" in src and "version=version_file" in src


def test_installer_script_consistency():
    iss = read("installer.iss")
    assert re.search(r"AppId=\{\{[0-9A-F]{8}(-[0-9A-F]{4}){3}-[0-9A-F]{12}\}", iss)
    for needle in ("PrivilegesRequired=lowest", "PrivilegesRequiredOverridesAllowed=dialog", "SetupIconFile=assets\\icon.ico",
                   "dist\\SerialMonitor\\*", "OutputBaseFilename=SerialMonitor-Setup-{#AppVersion}", "NeedsWebView2", "CloseApplications=yes"):
        assert needle in iss, needle
    assert iss.count("#ifexist") + iss.count("#ifndef") == iss.count("#endif") >= 2 and os.path.exists(P("assets", "icon.ico"))


def test_workflow_gates_build_on_tests_and_verifies_installer():
    wf = yaml.safe_load(read(".github", "workflows", "build-windows.yml"))
    jobs = wf["jobs"]; assert "test" in jobs and jobs["build"]["needs"] == "test"
    assert jobs["build"]["runs-on"] == "windows-latest" and "windows-latest" in str(jobs["test"]["strategy"]["matrix"]["os"])
    text = read(".github", "workflows", "build-windows.yml")
    for needle in ("pytest", "node --test", "--selftest", "--gui-selftest", "/VERYSILENT", "unins000.exe", "SHA256", "if-no-files-found: error"):
        assert needle in text, needle


def test_no_database_or_network_code_left():
    for f in ("server.py", "app.py"):
        src = read(f)
        for bad in ("sqlite", "import requests", "urllib.request", "socket.connect", "ftplib", "smtplib", "os.system", "shell=True", "eval(", "exec(", "pickle"):
            assert bad not in src, (f, bad)
    assert "sqlite" not in read("static", "index.html").lower() and "sqlite" not in read("requirements.txt").lower()


def test_python_sources_only_connect_to_loopback():
    for f in ("server.py", "app.py"):
        for host in re.findall(r"""["']((?:\d{1,3}\.){3}\d{1,3}|(?:https?://)?[a-z0-9.-]+\.(?:com|net|org|io))["']""", read(f)):
            assert host.startswith("127.0.0.1") or host.endswith("jsdelivr.net"), (f, host)


def test_all_python_compiles():
    for f in ("server.py", "app.py", "tools/make_icon.py", "tools/make_samples.py", "tools/make_version_info.py"): ast.parse(read(f))


def test_samples_present():
    for f in ("sample_log.csv", "sample_log.json", "sample_log.txt", "sample_log.bin"): assert os.path.getsize(P("samples", f)) > 1000
    assert len(os.listdir(P("samples", "split_demo_1min"))) == 3
