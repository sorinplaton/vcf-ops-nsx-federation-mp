"""Rewrite the registry address inside a built .pak (mp-build misparses host:port registries without a domain)."""
import io, sys, zipfile
pak, registry, repository = sys.argv[1], sys.argv[2], sys.argv[3]
src = zipfile.ZipFile(pak); out_buf = io.BytesIO()
with zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as out:
    for item in src.infolist():
        data = src.read(item.filename)
        if item.filename == "adapter.zip":
            inner = zipfile.ZipFile(io.BytesIO(data)); inner_buf = io.BytesIO()
            with zipfile.ZipFile(inner_buf, "w", zipfile.ZIP_DEFLATED) as inner_out:
                for it in inner.infolist():
                    d = inner.read(it.filename)
                    if it.filename.endswith(".conf") and "/" not in it.filename:
                        lines = []
                        for line in d.decode().splitlines():
                            if line.startswith("REGISTRY="): line = f"REGISTRY={registry}"
                            if line.startswith("REPOSITORY="): line = f"REPOSITORY=/{repository}"
                            lines.append(line)
                        d = ("\n".join(lines) + "\n").encode(); print(d.decode())
                    inner_out.writestr(it, d)
            data = inner_buf.getvalue()
        out.writestr(item, data)
open(pak, "wb").write(out_buf.getvalue()); print("rewrote", pak)
