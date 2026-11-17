#!/usr/bin/env python3
"""Builds VIGIL_Mathematical_Formulation.pdf.

Body text is Times-Roman (a built-in PDF font, so nothing needs embedding).
Formulas are rendered by matplotlib's mathtext using the STIX font set, which is
metrically designed to sit alongside Times -- reportlab has no maths typesetting
of its own, and hand-assembling these from Symbol-font glyphs would be both
fragile and worse-looking.
"""

import pathlib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether,
                                PageTemplate, Paragraph, Spacer)

plt.rcParams["mathtext.fontset"] = "stix"
plt.rcParams["font.family"] = "serif"
# Leave mathtext.default at "it": variables italic, \mathrm{} upright,
# which is the conventional maths setting and what pairs with Times.

HERE = pathlib.Path(__file__).resolve().parent
FIGS = HERE / "_formulas"
FIGS.mkdir(exist_ok=True)
OUT = "/Users/anmol117/Downloads/VIGIL_Mathematical_Formulation.pdf"
DPI = 400
_n = [0]
EQ_LOG = []        # (tex) in render order
_MARK = [0]


def eq(tex, size=12):
    """Render one display equation and return it as a correctly scaled Image."""
    _n[0] += 1
    EQ_LOG.append(tex)
    p = FIGS / f"eq{_n[0]:02d}.png"
    f = plt.figure(figsize=(0.01, 0.01))
    f.text(0, 0, tex, fontsize=size, color="black")
    f.savefig(p, dpi=DPI, bbox_inches="tight", pad_inches=0.02, transparent=True)
    plt.close(f)
    from PIL import Image as PILImage
    w, h = PILImage.open(p).size
    return Image(str(p), width=w * 72.0 / DPI, height=h * 72.0 / DPI)


st = getSampleStyleSheet()
S = {
    "title": ParagraphStyle("t", fontName="Times-Bold", fontSize=19, leading=24,
                            alignment=1, textColor=colors.black, spaceAfter=4),
    "sub": ParagraphStyle("s", fontName="Times-Roman", fontSize=11, leading=15,
                          alignment=1, textColor=colors.black, spaceAfter=20),
    "h": ParagraphStyle("h", fontName="Times-Bold", fontSize=12.5, leading=16,
                        textColor=colors.black, spaceBefore=16, spaceAfter=6),
    "body": ParagraphStyle("b", fontName="Times-Roman", fontSize=11, leading=15.5,
                           alignment=TA_JUSTIFY, textColor=colors.black, spaceAfter=7),
    "lab": ParagraphStyle("l", fontName="Times-Roman", fontSize=11, leading=15.5,
                          alignment=TA_JUSTIFY, textColor=colors.black,
                          spaceAfter=7, leftIndent=0),
    "note": ParagraphStyle("n", fontName="Times-Italic", fontSize=10.2, leading=14,
                           alignment=TA_JUSTIFY, textColor=colors.black,
                           spaceBefore=3, spaceAfter=7, leftIndent=10),
}


def P(t, s="body"):
    return Paragraph(t, S[s])


ITEMS = []          # (number, title, [tex, ...]) for the source appendix


def item(n, title, context, where, equations, verified):
    """One numbered formulation, kept on a single page where it fits."""
    ITEMS.append((n, title, EQ_LOG[_MARK[0]:]))
    _MARK[0] = len(EQ_LOG)
    blk = [P(f"{n}. {title}", "h"),
           P(f"<b>Context.</b> {context}", "lab"),
           P(f"<b>Where.</b> {where}", "lab"),
           Spacer(1, 4)]
    for e in equations:
        blk += [e, Spacer(1, 8)]
    blk += [P(f"<b>Verified.</b> {verified}", "note")]
    return KeepTogether(blk)


def footer(c, doc):
    c.saveState()
    c.setFont("Times-Roman", 9)
    c.drawString(20 * mm, 12 * mm, "VIGIL — Mathematical Formulation")
    c.drawRightString(A4[0] - 20 * mm, 12 * mm, "Page %d" % doc.page)
    c.setLineWidth(0.4)
    c.line(20 * mm, 15 * mm, A4[0] - 20 * mm, 15 * mm)
    c.restoreState()


story = [
    P("Mathematical Formulation", "title"),
    P("Formal statements supporting the VIGIL RFID and LoRa vehicle logging "
      "system, with placement in the paper and verification against the "
      "implementation and measured results.", "sub"),
]

# ---------------------------------------------------------------- 1
story.append(item(
    1, "Authenticated encryption",
    "Gives formal weight to the confidentiality, integrity and keyless-relay "
    "claims made for the encryption module.",
    "Section III-C, after &ldquo;&hellip;confidentiality and integrity in a single pass.&rdquo;",
    [eq(r"$(C,\,T) = \mathrm{GCM\text{-}Enc}_K(N,\,A,\,P)$"),
     eq(r"$P = \mathrm{GCM\text{-}Dec}_K(N,\,A,\,C,\,T)$"),
     eq(r"$\mathrm{accepted\ \ iff}\ \ T_{received} = T_{computed}$", 11)],
    "P is the RFID UID, A the 14-byte frame header, K the 256-bit pre-shared "
    "key and N a 12-byte nonce. Matches the implementation exactly. The message "
    "type byte within N is what separates a data-frame nonce from that of its "
    "own acknowledgement under the same key and counter."))

# ---------------------------------------------------------------- 2
story.append(item(
    2, "Nonce construction",
    "The frame header carries the counter as eight bytes while the nonce uses "
    "only the low seven. Stating the relationship removes an apparent "
    "inconsistency that a reviewer would otherwise be entitled to question, "
    "since nonce reuse under a single key is the principal failure mode of GCM.",
    "Section III-C, immediately following the nonce description.",
    [eq(r"$N = \mathrm{Type} \,\|\, D \,\|\, (C \,\mathrm{mod}\, 2^{56})$")],
    "Confirmed against the source: the nonce is type, device identifier and the "
    "low seven bytes of the counter. Uniqueness holds provided C &lt; 2<super>56</super>, "
    "which is 7.2 &times; 10<super>16</super> values per device and unreachable in the "
    "deployment lifetime."))

# ---------------------------------------------------------------- 3
story.append(item(
    3, "LoRa time on air",
    "Converts the transmission-time result from &ldquo;measured and consistent&rdquo; "
    "into agreement with the physical-layer model, which is a considerably "
    "stronger claim.",
    "Section IV-C, before the paragraph comparing measurement with computation.",
    [eq(r"$T_{sym} = \frac{2^{SF}}{BW}, \qquad T_{pre} = (n_{pre} + 4.25)\,T_{sym}$"),
     eq(r"$n_{payload} = 8 + \max\left(\left\lceil \frac{8PL - 4SF + 28 + 16\,CRC - 20\,IH}{4(SF - 2DE)} \right\rceil (CR + 4),\ 0\right)$", 11),
     eq(r"$T_{ToA} = T_{pre} + n_{payload}\,T_{sym}$")],
    "Re-derived independently at SF = 7, BW = 125 kHz, CR = 4/5. A 34-byte data "
    "frame gives n<sub>payload</sub> = 63 and 77.1 ms against 78.0 ms measured; a "
    "31-byte acknowledgement gives 58 and 71.9 ms against 72.8 ms. The residual "
    "is +0.9 ms on both frames, which is consistent with fixed SPI setup "
    "overhead rather than measurement error."))

# ---------------------------------------------------------------- 4
story.append(item(
    4, "End-to-end latency decomposition",
    "Defines precisely what the reported end-to-end figure contains, and lets "
    "the per-stage table be read as a budget that closes rather than a list of "
    "independent measurements.",
    "Section IV-C, before the per-stage latency table.",
    [eq(r"$T_{E2E} = T_{RFID} + T_{enc} + T_{LoRa,data} + T_{relay} + T_{serial} + T_{DB} + T_{LoRa,ack} + T_{queue}$", 10.5)],
    "The relay-out measurement is not an independent term: on the receiver "
    "endPacket() blocks for the duration of the transmission, so relay-out and "
    "the acknowledgement airtime are the same quantity and must not both be "
    "counted. With that correction the budget closes to 1.2 %: "
    "3.52 + 0.07 + 77.96 + 2.60 + 12.6 + 427.49 + 72.80 = 597.0 ms against a "
    "measured median of 604.4 ms."))

# ---------------------------------------------------------------- 5
story.append(item(
    5, "Cryptographic overhead",
    "Supports the claim that authenticated encryption is not a meaningful cost "
    "on a microcontroller gate node.",
    "Section IV-C, replacing the sentence stating that encryption is below "
    "0.01 % of end-to-end latency.",
    [eq(r"$T_{crypto} = T_{enc} + T_{dec} + T_{seal}$"),
     eq(r"$\eta_{crypto} = \frac{T_{crypto}}{T_{E2E}} \times 100 = \frac{0.16}{604.43} \times 100 \approx 0.027\ \%$")],
    "Report as under 0.03 %, not below 0.01 %. The latter holds only against "
    "the mean, which Section IV argues against using. Verification of the "
    "returning acknowledgement on the transmitter was not separately "
    "instrumented, so 0.16 ms is a lower bound across three of the four "
    "cryptographic stages."))

# ---------------------------------------------------------------- 6
story.append(item(
    6, "Cryptographic cost as a scale-invariant ratio",
    "An alternative to the percentage above, expressing the same argument as a "
    "ratio of two measured quantities so that it does not depend on whether the "
    "mean or the median is taken as the summary statistic.",
    "Section IV-C, as an alternative or a complement to the preceding item.",
    [eq(r"$\frac{T_{LoRa,data}}{T_{enc}} \approx 1100, \qquad \frac{T_{DB}}{T_{crypto}} \approx 2700$")],
    "Encryption is three orders of magnitude below the radio transmission it "
    "protects, and the complete cryptographic layer three orders below the "
    "database round trip."))

# ---------------------------------------------------------------- 7
story.append(item(
    7, "Stop-and-wait ARQ reliability",
    "Provides a reliability model for the acknowledgement scheme, which the "
    "paper currently describes only procedurally.",
    "Section III-D, after the description of the stop-and-wait discipline.",
    [eq(r"$P_{success}(R) = 1 - p_f^{\,R+1}, \qquad E[N] = \frac{1}{1 - p_f}$")],
    "p<sub>f</sub> can be estimated from the measurements rather than assumed: 32 scans "
    "were delivered over 39 attempts, of which 7 failed, giving "
    "p<sub>f</sub> = 0.179. The model then predicts E[N] = 1.219 against 39/32 = 1.219 "
    "measured. The 95 % confidence interval is [0.090, 0.327], which is wide "
    "because n = 39; report the estimate with that caveat. "
    "P(success within three attempts) = 99.4 %."))

# ---------------------------------------------------------------- 8
story.append(item(
    8, "Retransmission latency",
    "Shows that retransmission cost follows from the protocol constants rather "
    "than being an empirical observation, so it may be quoted as a design "
    "parameter.",
    "Section IV-C, before the paragraph on retransmission latency.",
    [eq(r"$T_N = (N-1)\left(T_a + T_{TO}\right) + \sum_{i=1}^{N-1} B_i + T_a + T_{ACK}$"),
     eq(r"$\mathrm{with}\ \ B_i = iB: \qquad T_N = (N-1)\left(T_a + T_{TO}\right) + \frac{B\,N(N-1)}{2} + T_a + T_{ACK}$", 10.5)],
    "N &minus; 1 attempts time out and one succeeds; charging a timeout to the "
    "successful attempt overstates the result by about 2.5 s. With "
    "T<sub>a</sub> = 78 ms, T<sub>TO</sub> = 3000 ms, B = 1000 ms and T<sub>ACK</sub> = 520 ms, "
    "the expression predicts 4676 ms for two attempts against 4678 and 4684 ms "
    "measured, and 9754 ms for three against 9708 and 9720 ms. The degenerate "
    "case N = 1 reduces to T<sub>a</sub> + T<sub>ACK</sub> = 598 ms against a measured "
    "first-attempt median of 604 ms. The expression holds for N &le; 3; beyond "
    "that the scan is re-queued behind a 30 s backoff."))

# ---------------------------------------------------------------- 9
story.append(item(
    9, "Idempotent event processing",
    "Formalises the property that makes retransmission safe, which is among the "
    "system's principal reliability contributions.",
    "Section III-D, after the sentence describing a lost acknowledgement.",
    [eq(r"$E = (D,\,C)$"),
     eq(r"$(D,C) \notin S \;\Rightarrow\; \mathrm{insert\ and\ commit}$"),
     eq(r"$(D,C) \in S \;\Rightarrow\; \mathrm{reject\ as\ duplicate}$")],
    "D is the device identifier, C the monotonic counter and S the set of "
    "committed event identifiers. The correct term is idempotent event "
    "processing, not exactly-once delivery: the network provides at-least-once "
    "delivery, and the uniqueness constraint makes the effect exactly-once for "
    "a given identifier. The distinction is one a networking reviewer will "
    "expect to see drawn."))

# ---------------------------------------------------------------- 10
story.append(item(
    10, "Frame efficiency",
    "Gives a defensible quantitative basis for discussing the cost of the "
    "security and reliability headers.",
    "Section III-C, after the frame structure table.",
    [eq(r"$L_{frame} = 14 + n + 16, \qquad \eta_{payload} = \frac{n}{L_{frame}} \times 100$")],
    "For a 4-byte UID, L<sub>frame</sub> = 34 bytes and efficiency is 11.76 %. Stated "
    "alone this appears poor, so give the absolute cost as well: the 30 bytes "
    "of header and authentication tag correspond to 46.1 ms of additional air "
    "time at SF 7. Efficiency improves to 18.9 % for a 7-byte UID."))

# ---------------------------------------------------------------- 11
story.append(item(
    11, "Read accuracy",
    "Formalises the distinction between the two accuracy measures the paper "
    "reports, which is otherwise carried only in prose.",
    "Section IV-B, before the read accuracy table.",
    [eq(r"$R_{attempt} = \frac{N_{decoded}}{N_{attempts}} \times 100, \qquad R_{presentation} = \frac{N_{successful}}{N_{detected}} \times 100$", 10.5)],
    "For the static condition, 49 of 51 attempts decoded gives 96.1 %, while 49 "
    "of 49 detected presentations succeeded, giving 100 %. Both figures are "
    "correct and measure different things: the first counts every call to the "
    "decode routine, the second counts distinct presentations, within which a "
    "failed attempt recovered on the next polling cycle loses no data."))

# ---------------------------------------------------------------- appendix
# The equations above are images, so they cannot be selected or extracted.
# This appendix carries their source as real text, for retyping into the paper.
from reportlab.platypus import PageBreak
S["src"] = ParagraphStyle("src", fontName="Courier", fontSize=8.2, leading=11,
                          textColor=colors.black, spaceAfter=6, leftIndent=8)
S["srch"] = ParagraphStyle("srch", fontName="Times-Bold", fontSize=10.5,
                           leading=14, textColor=colors.black,
                           spaceBefore=10, spaceAfter=3)
story.append(PageBreak())
story.append(P("Appendix: equation source", "title"))
story.append(P("The equations in this document are typeset as images and cannot "
               "be selected. Their LaTeX source is reproduced below for "
               "transcription into the paper.", "sub"))
for n, title, texs in ITEMS:
    if not texs:
        continue
    story.append(P(f"{n}. {title}", "srch"))
    for t in texs:
        esc = (t.strip("$").replace("&", "&amp;").replace("<", "&lt;")
               .replace(">", "&gt;").replace(" ", "&nbsp;"))
        story.append(Paragraph(esc, S["src"]))

doc = BaseDocTemplate(OUT, pagesize=A4, leftMargin=22 * mm, rightMargin=22 * mm,
                      topMargin=20 * mm, bottomMargin=20 * mm,
                      title="VIGIL — Mathematical Formulation", author="")
doc.addPageTemplates([PageTemplate(
    id="m", frames=[Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height)],
    onPage=footer)])
doc.build(story)
print("wrote", OUT)
