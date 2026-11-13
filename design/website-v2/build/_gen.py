src = open("../design/website-v2/mockup.html", encoding="utf-8").read()
css = src[src.index("<style>") + 7:src.index("</style>")]
css = css.replace("--brass:#b88a1f;", "--brass:#b88a1f;--brass-ink:#8f6a12;")
css = css.replace(".eyebrow{font:500 .72rem var(--sans);letter-spacing:.22em;text-transform:uppercase;color:var(--brass);",
                  ".eyebrow{font:500 .72rem var(--sans);letter-spacing:.22em;text-transform:uppercase;color:var(--brass-ink);")
css = css.replace(".hero .count", ".dark .eyebrow,.hero .eyebrow{color:var(--brass)}\n.hero .count")
css = css.replace("@media(prefers-reduced-motion:reduce){*{animation:none!important}}", "")
css += """
a:focus-visible,.btn:focus-visible,button:focus-visible,summary:focus-visible{outline:2px solid var(--orange);outline-offset:3px}
.skip{position:absolute;left:-999px}.skip:focus{left:12px;top:12px;background:#fff;padding:8px 12px;z-index:50}
.ver{color:var(--brass-ink);border-color:var(--brass-ink);text-decoration:none}
.clip{margin-top:40px;max-width:560px;border:1px solid #2e2c25;border-radius:12px;overflow:hidden;box-shadow:0 30px 60px -30px #000}
.clip video{display:block;width:100%;aspect-ratio:16/9;object-fit:cover;background:#000}
@media(max-width:899px){.clip{display:none}}
.reveal{opacity:0;transform:translateY(12px);transition:opacity .9s ease,transform .9s ease}
.reveal.in{opacity:1;transform:none}
.flow li{transition:color .6s,border-color .6s}
.demo video{width:100%;display:block;aspect-ratio:16/9;background:var(--night)}
.faq{max-width:760px}
.faq details{border-top:1px solid var(--line);padding:18px 0}.faq details:last-child{border-bottom:1px solid var(--line)}
.faq summary{cursor:pointer;font:400 1.25rem var(--serif)}.faq p{color:var(--mute);margin:12px 0 0;max-width:680px}
@media(prefers-reduced-motion:reduce){*{animation:none!important;scroll-behavior:auto!important}.reveal{opacity:1;transform:none;transition:none}}
"""
V = "v0.6.0"
REL = "https://github.com/mastalink/BitCadence/releases/tag/" + V


def shot(f, alt, cls="shot", lazy=True):
    l = ' loading="lazy"' if lazy else ""
    return f'<div class="{cls} reveal"><img src="img/{f}.webp" width="1280" height="800" alt="{alt}"{l}></div>'


tpl = open("_tpl.html", encoding="utf-8").read()
rep = {
    "@@CSS@@": css, "@@V@@": V, "@@REL@@": REL,
    "@@SHOT_JOBS@@": shot("04-console-job-board", "Job board in the BitCadence console", lazy=False),
    "@@SHOT_AUDIT@@": shot("10-console-activity-audit", "Activity and audit trail in the console"),
    "@@SHOT_DRUM@@": shot("09-console-drumline-memory", "Drumline shared memory in the console"),
    "@@SHOT_APPR@@": shot("05-console-approvals", "Approval queue in the console", "shot small"),
    "@@SHOT_HELP@@": shot("08-console-helpers", "Helpers screen with health light", "shot small"),
    "@@SHOT_ASK@@": shot("21-console-ask", "Ask for something screen", "shot small"),
}
for k, v in rep.items():
    tpl = tpl.replace(k, v)
open("../../../website/index.html", "w", encoding="utf-8").write(tpl)
