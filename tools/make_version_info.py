"""Writes a Windows VERSIONINFO resource (shown in Explorer > Properties and used by IT allow-listing)."""
def build_text(version, company="Serial Monitor"):
    nums = tuple(int(x) for x in version.split(".")[:3]) + (0,)
    rows = [("CompanyName", company), ("FileDescription", "Serial Monitor - serial port viewer and logger"),
            ("FileVersion", version), ("InternalName", "SerialMonitor"), ("OriginalFilename", "SerialMonitor.exe"),
            ("ProductName", "Serial Monitor"), ("ProductVersion", version)]
    body = ",\n      ".join("StringStruct(u'%s', u'%s')" % kv for kv in rows)
    return ("# UTF-8\nVSVersionInfo(\n  ffi=FixedFileInfo(filevers=%s, prodvers=%s, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),\n"
            "  kids=[\n    StringFileInfo([StringTable(u'040904B0', [\n      %s\n    ])]),\n    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])\n  ]\n)\n") % (nums, nums, body)


def write(path, version):
    with open(path, "w", encoding="utf-8") as f: f.write(build_text(version))
