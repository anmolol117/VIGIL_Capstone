// node build.js <figs dir> <out.docx>
const fs = require("fs");
const L = require("./lib.js");
const { D, FONT, FIGS, TABS, placed, citeOrder, REFS } = L;
const { Document, Packer, Paragraph, TextRun, AlignmentType, Footer, PageNumber, NumberFormat, LevelFormat, SectionType } = D;

const [FIG, OUT] = process.argv.slice(2);
FIGS.push("arch", "gate_arch", "gate_flow", "speed_pipeline", "speed_frame", "speed_console", "parking_pipeline",
  "parking_dashboard", "parking_sign", "campus_app", "guard_app", "web_home", "gantt");
TABS.push("compare", "gate_hw", "radio", "frame", "ack", "speed_params", "park_params", "status_levels", "stack",
  "rfid_latency", "rfid_read", "rfid_db", "rfid_unauth", "spd_stab", "spd_clean", "spd_timing", "spd_scale", "spd_perf",
  "plan", "db_tables", "ports");

const F = require("./front.js");
const { ch1, ch2, ch3 } = require("./ch123.js");
const ch4 = require("./ch4.js");
const { ch5, ch6, ch7, references, appendix } = require("./ch567.js");

const body = [...ch1(), ...ch2(), ...ch3(), ...ch4(FIG), ...ch5(), ...ch6(FIG), ...ch7()];
const back = [...references(), ...appendix()];

// every registered figure and table must appear, in the registered order
const same = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);
if (!same(placed.fig, FIGS)) throw new Error(`figure order ${placed.fig} != ${FIGS}`);
if (!same(placed.tab, TABS)) throw new Error(`table order ${placed.tab} != ${TABS}`);
const unused = Object.keys(REFS).filter(k => !citeOrder.includes(k));

const A4 = { width: 11906, height: 16838 };
const margin = { top: 1440, bottom: 1440, left: 2160, right: 1440, header: 708, footer: 708 };
const footer = () => ({ default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER,
  children: [new TextRun({ children: [PageNumber.CURRENT], font: FONT })] })] }) });
const emptyFooter = { default: new Footer({ children: [new Paragraph("")] }) };

const doc = new Document({
  creator: "VIGIL project team", title: F.TITLE, description: "B.Tech capstone project report, TIET Patiala",
  features: { updateFields: process.env.NO_UPDATE || L.GDOCS ? false : true },
  styles: {
    default: {
      document: { run: { font: FONT, size: 24 }, paragraph: { spacing: { line: 360 } } },
      heading1: { run: { font: FONT, size: 32, bold: true, color: "000000" }, paragraph: { alignment: AlignmentType.CENTER, spacing: { before: 0, after: 480 }, keepNext: true } },
      heading2: { run: { font: FONT, size: 24, bold: true, color: "000000" }, paragraph: { spacing: { before: 240, after: 120 }, keepNext: true } },
      heading3: { run: { font: FONT, size: 24, bold: true, italics: false, color: "000000" }, paragraph: { spacing: { before: 180, after: 80 }, keepNext: true } },
    },
    paragraphStyles: [
      { id: "Caption", name: "caption", basedOn: "Normal", next: "Normal", run: { font: FONT, size: 24, color: "000000" } },
    ],
  },
  numbering: { config: [
    { reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: 567, hanging: 283 } }, run: { font: FONT } } }] },
    { reference: "nums", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: 567, hanging: 397 } }, run: { font: FONT } } }] },
  ] },
  sections: [
    { properties: { page: { size: A4, margin } }, footers: emptyFooter, children: [...F.cover(FIG), ...F.declaration(), ...F.certificate()] },
    { properties: { type: SectionType.NEXT_PAGE, page: { size: A4, margin, pageNumbers: { start: 1, formatType: NumberFormat.LOWER_ROMAN } } },
      footers: footer(), children: [...F.acknowledgments(), ...F.abstract(), ...F.contents(), ...F.abbreviations(), ...F.notation()] },
    { properties: { type: SectionType.NEXT_PAGE, page: { size: A4, margin, pageNumbers: { start: 1, formatType: NumberFormat.DECIMAL } } },
      footers: footer(), children: [...body, ...back] },
  ],
});

Packer.toBuffer(doc).then(buf => {
  fs.writeFileSync(OUT, buf);
  console.log(`wrote ${OUT}: ${FIGS.length} figures, ${TABS.length} tables, ${citeOrder.length} references cited`);
  if (unused.length) console.log("not cited (left out of the list):", unused.join(", "));
});
