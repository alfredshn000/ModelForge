"""把 David.mdx 导出成前端可直接渲染的数据包（model.js）。

打包原则：
- 顶点 / 索引 / 蒙皮 / 动画全部 base64 存二进制，不做 JSON 浮点（体积小一半且无精度损失）
- 索引用 u16（每个 geoset 顶点数都 < 65536）
- 动画只传原始关键帧，不预烘焙；前端按需线性插值（旋转走 slerp）
- 蒙皮权重归一化到 1：MDX 里是 0–255，和可能不足 255，不归一化模型会缩水
"""

from __future__ import annotations

import base64
import json
import pathlib
import struct
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mdx-toolkit"))

from mdxcore import load                                  # noqa: E402
from mdxcore import skin as SK                            # noqa: E402
from mdxcore.chunks import bone as B                      # noqa: E402
from mdxcore.chunks import geos as G                      # noqa: E402
from mdxcore.chunks import pivt as P                      # noqa: E402
from mdxcore.ops import format_version, list_sequences     # noqa: E402

MODEL = next((ROOT / "test-models").rglob("David.mdx"))  # 可用 argv 覆盖
OUT = pathlib.Path(__file__).resolve().parent / "model.js"

TAG_TYPE = {"KGTR": 0, "KGRT": 1, "KGSC": 2}


def b64(fmt: str, vals) -> str:
    return base64.b64encode(struct.pack("<" + fmt * len(vals), *vals)).decode()


def main() -> None:
    # 参数：argv[1]=模型路径  argv[2]=输出目录（可选）
    global MODEL, OUT, OUT_DIR
    if len(sys.argv) > 1:
        MODEL = pathlib.Path(sys.argv[1])
    if len(sys.argv) > 2:
        _d = pathlib.Path(sys.argv[2])
        _d.mkdir(parents=True, exist_ok=True)
        OUT = _d / OUT.name
        OUT_DIR = _d / 'tex'
    mdx = load(MODEL)
    ver = format_version(mdx)

    has = lambda tag: any(c.tag == tag for c in mdx.chunks)
    pivots = P.parse(mdx.data(mdx.find("PIVT"))) if has("PIVT") else []
    bdata = mdx.data(mdx.find("BONE")) if has("BONE") else b""
    bones = B.parse(bdata) if bdata else []

    # ---- 骨骼 ----
    blist = []
    for b in bones:
        pv = pivots[b.object_id].xyz if 0 <= b.object_id < len(pivots) else (0.0, 0.0, 0.0)
        blist.append({
            "n": b.name,
            "id": b.object_id,
            "p": b.parent_id,
            "pv": [round(float(v), 4) for v in pv],
        })

    # ---- 几何 ----
    geosets = []
    total_v = total_t = 0
    lo = [1e18, 1e18, 1e18]
    hi = [-1e18, -1e18, -1e18]
    palette = ["#7f93a8", "#8e9cab", "#75899d", "#9aa7b4", "#6f8398", "#8695a6"]
    geoset_list = G.parse(mdx.data(mdx.find("GEOS")), ver) if has("GEOS") else []
    for gi, g in enumerate(geoset_list):
        nv = g.vertex_count
        total_v += nv
        total_t += len(g.faces) // 3

        pos = [c for v in g.vertices for c in v]
        for k in range(0, len(pos), 3):
            for ax in range(3):
                v = pos[k + ax]
                if v < lo[ax]:
                    lo[ax] = v
                if v > hi[ax]:
                    hi[ax] = v
        nrm = [c for v in g.normals for c in v] if g.normals else [0.0] * (nv * 3)
        if len(nrm) < nv * 3:
            nrm = (nrm + [0.0] * (nv * 3))[: nv * 3]

        if g.uv_sets and g.uv_sets[0]:
            uv = [c for p in g.uv_sets[0] for c in p]
        else:
            uv = [0.0] * (nv * 2)
        if len(uv) < nv * 2:
            uv = (uv + [0.0] * (nv * 2))[: nv * 2]

        idx = list(g.faces)

        # 蒙皮：取权重最大的 4 个，归一化
        infl = SK.influences(g)
        jnt, wgt, no_skin = [], [], 0
        for vi in range(nv):
            cand = infl[vi] if vi < len(infl) else []
            top = sorted(cand, key=lambda x: -x.weight)[:4]
            s = sum(x.weight for x in top)
            if s <= 0:
                no_skin += 1
                jnt += [0, 0, 0, 0]
                wgt += [1.0, 0.0, 0.0, 0.0]
                continue
            js = [x.node_index for x in top] + [0] * (4 - len(top))
            ws = [x.weight / s for x in top] + [0.0] * (4 - len(top))
            jnt += js
            wgt += ws

        geosets.append({
            "i": gi,
            "mat": g.material_id,
            "nv": nv,
            "nt": len(g.faces) // 3,
            "col": palette[gi % len(palette)],
            "pos": b64("f", pos),
            "nrm": b64("f", nrm),
            "uv": b64("f", uv),
            "idx": b64("H", idx),
            "jnt": b64("H", jnt),
            "wgt": b64("f", wgt),
            "skinless": no_skin,
        })

    # ---- 动画轨道 ----
    tracks = []
    nkeys = 0
    for bi, b in enumerate(bones):
        for tag, flag in (B.parse_tracks(bdata, b).items() if bdata else []):
            typ = TAG_TYPE.get(tag)
            if typ is None:
                continue
            keys = []
            for t in flag.tracks:
                keys.append(int(t.time))
                keys.extend(float(c) for c in t.value)
            if not keys:
                continue
            nkeys += len(flag.tracks)
            tracks.append({
                "b": bi,
                "t": typ,
                "n": len(flag.tracks),
                "s": 5 if tag == "KGRT" else 4,   # 旋转是 (time,x,y,z,w)，其余 (time,x,y,z)
                "interp": int(flag.interp),
                "k": b64("f", keys),
            })

    # ---- 粒子发射器（PRE2）----
    particles = []
    if any(c.tag == "PRE2" for c in mdx.chunks):
        from mdxcore.chunks import pre2 as pre2mod
        try:
            for e in pre2mod.parse(mdx.data(mdx.find("PRE2"))):
                if not e.active:
                    continue
                particles.append({
                    "i": e.index, "n": e.name, "obj": e.object_id, "par": e.parent_id,
                    "rate": e.emission_rate, "life": e.lifespan, "speed": e.speed,
                    "var": e.variation, "lat": e.latitude, "grav": e.gravity,
                    "len": e.length, "wid": e.width,
                    "rows": e.rows, "cols": e.columns, "filt": e.filter_mode,
                    "tex": e.texture_id,
                    "col": [[round(c, 4) for c in rgb] for rgb in e.colors],
                    "alpha": [round(a, 3) for a in e.alphas],
                })
        except Exception as ex:
            print(f"    粒子解析跳过：{ex}")

    seqs = [{"i": i, "n": s.name, "s": s.start, "e": s.end, "loop": not s.non_looping}
            for i, s in enumerate(list_sequences(mdx))]

    payload = {
        "file": MODEL.name,
        "ver": ver,
        "bytes": len(mdx.raw),
        "bones": blist,
        "geosets": geosets,
        "tracks": tracks,
        "particles": particles,
        "sequences": seqs,
        "bounds": {"min": [round(v, 3) for v in lo], "max": [round(v, 3) for v in hi]},
        "stats": {"verts": total_v, "tris": total_t, "bones": len(blist), "keys": nkeys},
    }

    text = "window.MDX_MODEL = " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n"
    OUT.write_text(text, encoding="utf-8")

    print(f"写出 {OUT}  {OUT.stat().st_size / 1048576:.2f} MB")
    print(f"  geoset {len(geosets)}  顶点 {total_v:,}  三角 {total_t:,}  "
          f"骨骼 {len(blist)}  轨道 {len(tracks)}  关键帧 {nkeys:,}")
    if particles:
        print(f"  粒子发射器 {len(particles)}："
              + ", ".join(f"{e['n']}({e['rate']:.0f}/s)" for e in particles))
    print(f"  无蒙皮顶点 {sum(g['skinless'] for g in geosets)}")
    print(f"  包围盒 min={[round(v,1) for v in lo]} max={[round(v,1) for v in hi]}")


if __name__ == "__main__":
    main()
