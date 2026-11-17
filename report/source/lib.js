// Layout helpers for the VIGIL capstone report: TIET format (Times New Roman 12,
// chapter headings 16, 1.5 spacing, justified), IEEE numbered citations in order
// of first use, and "Figure N." / "Table N." captions on SEQ fields.
const fs = require("fs");
const path = require("path");
const D = require("docx");
const {
  Paragraph, TextRun, HeadingLevel, AlignmentType, Table, TableRow, TableCell, WidthType,
  BorderStyle, ImageRun, SequentialIdentifier, ShadingType, VerticalAlign, TabStopType,
  Math: OMath, MathRun,
} = D;

const FONT = "Times New Roman";
const TEXT_W = 8306;          // A4 minus 1.5" left and 1" right margins, in DXA
const LINE = 360;             // 1.5 line spacing
// GDOCS=1: no Word-only fields or equation objects, so the file imports cleanly into Google Docs
const GDOCS = !!process.env.GDOCS;
const captions = [];          // [kind, number, text], for static lists of figures and tables

const REFS = require("./refs.js");
const citeOrder = [];
function citeNums(keys) {
  const n = keys.map(k => {
    if (!REFS[k]) throw new Error("unknown reference " + k);
    if (!citeOrder.includes(k)) citeOrder.push(k);
    return citeOrder.indexOf(k) + 1;
  }).sort((a, b) => a - b);
  // [1]–[3] for runs of three or more, otherwise [1], [2]
  const out = [];
  for (let i = 0; i < n.length;) {
    let j = i;
    while (j + 1 < n.length && n[j + 1] === n[j] + 1) j++;
    if (j - i >= 2) out.push(`[${n[i]}]–[${n[j]}]`);
    else for (let k = i; k <= j; k++) out.push(`[${n[k]}]`);
    i = j + 1;
  }
  return out.join(", ");
}

// figure and table numbers are fixed up front, so text can refer forward
const FIGS = [], TABS = [];
const figNo = id => { const i = FIGS.indexOf(id); if (i < 0) throw new Error("unknown figure " + id); return i + 1; };
const tabNo = id => { const i = TABS.indexOf(id); if (i < 0) throw new Error("unknown table " + id); return i + 1; };
const placed = { fig: [], tab: [] };

// Inline markup: **bold**, *italic*, {{refkey,refkey}}, {{fig:id}}, {{tab:id}}
function runs(text, base = {}) {
  text = text.replace(/\{\{([^}]+)\}\}/g, (_, body) => {
    if (body.startsWith("fig:")) return `Figure ${figNo(body.slice(4))}`;
    if (body.startsWith("tab:")) return `Table ${tabNo(body.slice(4))}`;
    return citeNums(body.split(",").map(s => s.trim()));
  });
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*)/).filter(Boolean);
  return parts.map(p => {
    if (p.startsWith("**")) return new TextRun({ text: p.slice(2, -2), bold: true, font: FONT, ...base });
    if (p.startsWith("*") && p.length > 2) return new TextRun({ text: p.slice(1, -1), italics: true, font: FONT, ...base });
    return new TextRun({ text: p, font: FONT, ...base });
  });
}

const P = (text, o = {}) => new Paragraph({
  alignment: o.align ?? AlignmentType.JUSTIFIED,
  spacing: { line: o.line ?? LINE, before: o.before ?? 0, after: o.after ?? 120 },
  keepNext: o.keepNext, pageBreakBefore: o.pageBreakBefore, indent: o.indent,
  children: runs(text, o.run),
});

const bullets = items => items.map(t => new Paragraph({
  numbering: { reference: "bullets", level: 0 }, alignment: AlignmentType.JUSTIFIED,
  spacing: { line: LINE, after: 60 }, children: runs(t),
}));
let listInstance = 0;
const numbered = items => { const inst = ++listInstance; return items.map(t => new Paragraph({
  numbering: { reference: "nums", level: 0, instance: inst }, alignment: AlignmentType.JUSTIFIED,
  spacing: { line: LINE, after: 60 }, children: runs(t),
})); };

let chapterNo = 0;
const chapter = title => new Paragraph({
  heading: HeadingLevel.HEADING_1, pageBreakBefore: true, alignment: AlignmentType.CENTER,
  spacing: { line: LINE, after: 480 },
  children: [new TextRun({ text: `CHAPTER ${++chapterNo}`, font: FONT }),
             new TextRun({ text: title, font: FONT, break: 1 })],
});
const front = (title, o = {}) => new Paragraph({
  heading: o.noToc ? undefined : HeadingLevel.HEADING_1, pageBreakBefore: o.pageBreakBefore ?? true,
  alignment: AlignmentType.CENTER, spacing: { line: LINE, after: 360 },
  children: [new TextRun({ text: title, font: FONT, bold: true, size: 32 })],
});
const h2 = t => new Paragraph({ heading: HeadingLevel.HEADING_2, keepNext: true, spacing: { line: LINE, before: 240, after: 120 }, children: runs(t) });
const h3 = t => new Paragraph({ heading: HeadingLevel.HEADING_3, keepNext: true, spacing: { line: LINE, before: 180, after: 80 }, children: runs(t) });

function imgSize(buf) {
  if (buf[0] === 0x89) return { w: buf.readUInt32BE(16), h: buf.readUInt32BE(20) };
  for (let i = 2; i < buf.length;) {               // JPEG: find the frame header
    if (buf[i] !== 0xff) { i++; continue; }
    const m = buf[i + 1], len = buf.readUInt16BE(i + 2);
    if (m >= 0xc0 && m <= 0xc3) return { h: buf.readUInt16BE(i + 5), w: buf.readUInt16BE(i + 7) };
    i += 2 + len;
  }
  throw new Error("unknown image");
}

const caption = (kind, text, n) => (captions.push([kind, n, text]), new Paragraph({
  style: "Caption", alignment: AlignmentType.CENTER, keepNext: kind === "Table",
  spacing: { line: 276, before: kind === "Table" ? 160 : 80, after: kind === "Table" ? 100 : 200 },
  children: [new TextRun({ text: `${kind} `, bold: true, font: FONT }),
             GDOCS ? new TextRun({ text: String(n), bold: true, font: FONT }) : new SequentialIdentifier(kind),
             new TextRun({ text: ". ", bold: true, font: FONT }), ...runs(text)],
}));

function figure(dir, id, file, widthIn, text) {
  placed.fig.push(id);
  const buf = fs.readFileSync(path.join(dir, file)), s = imgSize(buf);
  const w = Math.round(widthIn * 96), h = Math.round(w * s.h / s.w);
  return [
    new Paragraph({ alignment: AlignmentType.CENTER, keepNext: true, spacing: { before: 120, after: 0 },
      children: [new ImageRun({ type: file.endsWith(".png") ? "png" : "jpg", data: buf, transformation: { width: w, height: h } })] }),
    caption("Figure", text, placed.fig.length),
  ];
}

const border = { style: BorderStyle.SINGLE, size: 4, color: "000000" };
const borders = { top: border, bottom: border, left: border, right: border };
function table(id, text, header, rows, widths, o = {}) {
  placed.tab.push(id);
  const sum = widths.reduce((a, b) => a + b, 0);
  if (sum !== TEXT_W) widths = widths.map(w => Math.round(w * TEXT_W / sum));
  const cell = (t, i, head) => new TableCell({
    width: { size: widths[i], type: WidthType.DXA }, borders, verticalAlign: VerticalAlign.CENTER,
    shading: head ? { type: ShadingType.CLEAR, fill: "E7E6E6", color: "auto" } : undefined,
    margins: { top: 50, bottom: 50, left: 90, right: 90 },
    children: [new Paragraph({ alignment: (o.align && o.align[i]) || AlignmentType.LEFT, spacing: { line: 240, after: 0 },
      children: runs(String(t), head ? { bold: true } : {}) })],
  });
  return [
    caption("Table", text, placed.tab.length),
    new Table({ width: { size: TEXT_W, type: WidthType.DXA }, columnWidths: widths,
      rows: [new TableRow({ tableHeader: true, children: header.map((t, i) => cell(t, i, true)) }),
             ...rows.map(r => new TableRow({ cantSplit: true, children: r.map((t, i) => cell(t, i, false)) }))] }),
    new Paragraph({ spacing: { after: 120 }, children: [] }),
  ];
}

let eqNo = 0;
const eq = text => new Paragraph({
  tabStops: [{ type: TabStopType.CENTER, position: TEXT_W / 2 }, { type: TabStopType.RIGHT, position: TEXT_W }],
  spacing: { line: LINE, before: 60, after: 120 },
  children: [new TextRun({ text: "\t", font: FONT }),
             GDOCS ? new TextRun({ text, italics: true, font: FONT }) : new OMath({ children: [new MathRun(text)] }),
             new TextRun({ text: `\t(${++eqNo})`, font: FONT })],
});

// two-column list without borders, for abbreviations and notation
function glossary(rows, w0 = 1800) {
  const none = { style: BorderStyle.NONE, size: 0, color: "FFFFFF" };
  const nb = { top: none, bottom: none, left: none, right: none };
  const widths = [w0, TEXT_W - w0];
  return new Table({ width: { size: TEXT_W, type: WidthType.DXA }, columnWidths: widths,
    rows: rows.map(r => new TableRow({ children: r.map((t, i) => new TableCell({
      width: { size: widths[i], type: WidthType.DXA }, borders: nb, margins: { top: 20, bottom: 20, left: 60, right: 60 },
      children: [new Paragraph({ spacing: { line: 300, after: 0 }, children: runs(t, i === 0 ? { bold: true } : {}) })] })) })) });
}

module.exports = { D, FONT, TEXT_W, LINE, GDOCS, captions, REFS, citeOrder, FIGS, TABS, placed, runs, P, bullets, numbered,
  chapter, front, h2, h3, figure, table, eq, glossary, figNo, tabNo };
