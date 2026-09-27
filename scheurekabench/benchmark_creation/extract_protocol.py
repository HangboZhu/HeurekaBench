"""Extract a Nature Protocols article's Procedure into the protocol.json that
protocol_to_questions.py generates ProtocolQA questions from.

Why this exists: a LAB-Bench ProtocolQA entry is a troubleshooting question
paired with the protocol text it is asked about — the harness prepends that
text to the question (`input.question = self.protocol + input.question`) and
scores the answer as a letter choice over ideal + distractors. So the
generation unit is not an *insight* (a finding) but a *block of steps*: this
script produces those blocks.

What is written to <out_dir>/protocol.json:

  paper_id / doi / title / source_pdf   provenance (the official ProtocolQA
                                        envelope has no room for it: `source`
                                        is null in all 108 official items)
  blocks                                {Block1: {title, step_range, n_steps,
                                        protocol, troubleshooting}}
  troubleshooting_unmatched             table rows whose step reference could
                                        not be mapped onto a block

and also procedure_clean.txt (the cleaned Procedure text, for eyeballing).

Two properties matter downstream and are therefore preserved deliberately:

* **The protocol text is a faithful slice of the paper.** Only page furniture
  is removed (running heads, page numbers, figure captions and their panel
  letters/axis numbers) and whitespace is normalized. No reagent, volume,
  temperature or step order is ever altered: a question pack ships this text
  as instructions, so an invented value would be actively harmful.
* **Blocks follow the paper's own sub-procedure headings** ("Stage 2. Tissue
  homogenization", "Section 3: characterization ..."), so each block is a
  coherent procedure that can stand alone as one `protocol` field. Printed
  step numbers are kept as-is (never renumbered) because the paper's own
  cross-references ("repeat Step 3") point at them.

The paper's Troubleshooting table (Step / Problem / Possible reason /
Solution) is captured alongside: it is the journal's own expert endorsement of
a problem -> remedy pair, which is what makes a generated gold answer
defensible. It is generator-side grounding only — the official envelope has no
field for it and the respondent must never see it.

  python extract_protocol.py --pdf <dir>/paper.pdf --out <dir> --paper-id vine-seq
"""

import os
import re
import json
import argparse
from collections import Counter, OrderedDict

import fitz  # PyMuPDF

# A heading sits above body text — 9.75 pt sub-headings ("Stage 2. Tissue
# homogenization") and 11.25 pt top-level ones ("Procedure 1: ...") against a
# 9.0 pt body. SIZE_EPSILON matters: 9.0 + 0.75 is not exactly 9.75 in binary
# floating point, which would reject a heading by an invisible margin.
HEADING_SIZE_DELTA = 0.5
SIZE_EPSILON = 1e-6

# Some headings carry no size change at all: the Cu paper's "Section 3: ..."
# labels and the organoid paper's late sub-headings ("Intensity measurement")
# are semibold at body size. So a semibold line counts as a heading too — the
# reference implementation of this style check looks for "Semibold" in the font
# name, which is what every one of them uses.
BOLD_FONT_RE = re.compile(r"bold|black|heavy|semibold", re.I)
BOLD_HEADING_MAX_CHARS = 120

# Text set this far below the body belongs to a table or figure (Nature
# Protocols sets table columns at 7.0 pt against 9.0 pt body), not to the
# procedure; the ● TIMING lines, one point down at 8.0 pt, are kept.
SUBSIZE_DELTA = 1.5

# Below this much text, a block without numbered steps is figure residue that
# slipped through the caption filters rather than a procedure written without
# step numbers.
MIN_UNNUMBERED_CHARS = 400

# House-style callout markers. Nature Protocols sets the label without a space
# after it ("▲ CAUTIONElution buffer contains ..."), which otherwise reads as
# one nonsense word and defeats any label matching.
CANARY_GLYPHS = "\u25b2\u25cf\u25a0\u25c6"      # ▲ ● ■ ◆
CALLOUT_LABELS = ("CRITICAL STEP", "CRITICAL", "CAUTION", "TROUBLESHOOTING",
                  "TIMING", "PAUSE POINT")
_CALLOUT_SPLIT_RE = re.compile(
    r"^(?:[" + CANARY_GLYPHS + r"]\s*)?(" + "|".join(CALLOUT_LABELS) + r")\s*:?\s*(.*)$")

# The Procedure ends where the next top-level section starts. Matched against
# heading-sized lines only, so the word "Timing" inside a step does not cut.
END_HEADINGS = {
    "troubleshooting", "timing", "anticipated results", "references",
    "data availability", "code availability", "statistics",
    "supplementary information", "author contributions",
}

RUNNING_HEAD_RE = re.compile(r"^Nature Protocols\b")
RUNNING_HEAD_TAIL = {"protocol", "nature protocols"}
FIG_CAPTION_RE = re.compile(r"^Fig(?:ure)?\.?\s*\d+\s*\|")
TABLE_CAPTION_RE = re.compile(r"^Table\s*\d+\s*\|")
FIGURE_TAIL_RE = re.compile(r"^(?:Extended Data|Supplementary)\s+(?:Fig|Table)")
# A caption's continuing lines carry a bare panel label ("d, The optical ..."),
# and a figure's own labels are glued to their text ("cThin layer") — the
# no-space form is required so a sentence starting with an article survives.
PANEL_LABEL_RE = re.compile(r"^[a-z](?:[,\s]|[A-Z])")
PANEL_LETTER_RE = re.compile(r"^[a-zA-Z]$")
NUMBER_ONLY_RE = re.compile(r"^\d{1,3}(?:\.\d+)?$")

# Figure labels are set as runs of short lines with no sentence punctuation.
# Requirement (both): every line short, and nothing that looks like prose.
FIGURE_LABEL_MAX_CHARS = 40

# Bare "12." on its own line (steps 1-9 are set this way in all three papers)
# vs "12.\tText" inline.
STEP_BARE_RE = re.compile(r"^(\d{1,3})\.$")
STEP_INLINE_RE = re.compile(r"^(\d{1,3})\.\s+(.*)$")
# Option labels inside a procedure ("Option C: applying active site stabilizer",
# used by the Cu paper's Procedures 3 and 4). They subdivide a procedure rather
# than start one, so they are text, not headings: treating them as headings cut
# Procedure 4 into fragments of one and two steps each, which cannot carry a
# troubleshooting question the way a whole procedure can.
OPTION_LABEL_RE = re.compile(r"^Option [A-Z]\b", re.IGNORECASE)

# Sub-items that start their own line inside a step: (i) (ii) (A) (a), bullets.
SUBITEM_RE = re.compile(r"^(?:\([ivx]+\)|\([A-Z]\)|\([a-z]\)|[-•●◆▲■])\s*")
CALLOUT_RE = re.compile(r"^(?:" + "|".join(CALLOUT_LABELS) + r"):")
# A TIMING line times the whole stage, so it belongs to the section rather than
# folding into whichever step happens to precede it.
TIMING_RE = re.compile(r"^TIMING:")


def normalize_inline(text):
    """Collapse the whitespace of one line. Tabs separate the step number from
    its text in the PDF (and one protocol dialect uses U+2028 as a line break),
    so they become plain spaces here; nothing is deleted."""
    text = text.replace("\u2028", " ").replace("\u2029", " ")
    text = text.replace("\t", " ").replace("\u2009", " ").replace("\u2002", " ")
    text = text.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip()


def normalize_callout(text):
    """Rewrite "▲ CRITICAL STEPThe ratio must be exactly ..." as
    "CRITICAL STEP: The ratio must be exactly ...".

    Only the house-style marker is changed — no instruction is added, removed
    or reworded."""
    match = _CALLOUT_SPLIT_RE.match(text)
    if not match:
        return text
    label, rest = match.group(1), match.group(2)
    return f"{label}: {rest}".strip() if rest else f"{label}:"


def page_lines(page):
    """Furniture-free reading-order lines of one page as (lines, dropped).

    Filtering happens at *block* level, not line level: Nature Protocols puts
    figure captions (with their axis tokens and panel labels) in their own text
    blocks next to the step text, and a line-level rule cannot tell a caption's
    "3." from a step number once the two are interleaved — which folded whole
    captions into steps."""
    kept, dropped = [], Counter()
    blocks = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:          # image block
            continue
        lines = []
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            lines.append({
                "text": normalize_callout(normalize_inline(
                    "".join(s["text"] for s in spans))),
                "size": max(s["size"] for s in spans),
                "font": next((s["font"] for s in spans
                              if s["size"] == max(t["size"] for t in spans)), ""),
            })
        if lines:
            blocks.append(lines)

    # The running head is the topmost block(s) of the page, and PyMuPDF may
    # split "Nature Protocols | 15 | Protocol" into two of them.
    for i, lines in enumerate(blocks[:2]):
        first = lines[0]["text"].strip()
        if RUNNING_HEAD_RE.match(first) or first.lower() in RUNNING_HEAD_TAIL:
            dropped["running-head"] += len(lines)
            blocks[i] = []

    body = body_size([l for lines in blocks for l in lines])
    for i, lines in enumerate(blocks):
        if not lines:
            continue
        texts = [l["text"] for l in lines]
        has_caption_marker = any(FIG_CAPTION_RE.match(t) or TABLE_CAPTION_RE.match(t)
                                 or FIGURE_TAIL_RE.match(t) for t in texts)
        # A wrapped caption whose "Fig. 3 |" prefix sits in another block still
        # betrays itself with a bare panel label ("e, Example of induced ...");
        # step text never starts that way, but a step list might, hence the
        # looks_like_steps escape.
        looks_like_steps = any(STEP_BARE_RE.match(t) or STEP_INLINE_RE.match(t) or
                               SUBITEM_RE.match(t) for t in texts)
        has_panel_label = any(PANEL_LABEL_RE.match(t) for t in texts)
        if has_caption_marker or (has_panel_label and not looks_like_steps):
            dropped["figure-caption"] += len(lines)
            blocks[i] = []
            continue
        if all(len(t) <= 4 for t in texts):
            dropped["figure-token"] += len(lines)
            blocks[i] = []
            continue
        # Labels inside a figure ("cThin layer", "Multiple blobs", "Before
        # breaking"): all short, no sentence punctuation, no step structure. A
        # heading is short too, so it has to be excluded explicitly — otherwise
        # the section headings this whole script keys on disappear as "labels".
        if all(len(t) <= FIGURE_LABEL_MAX_CHARS for t in texts) and \
                not looks_like_steps and \
                not any(is_heading(l, body) for l in lines) and \
                not any(CALLOUT_RE.match(t) or TIMING_RE.match(t) for t in texts) and \
                not any(t.rstrip().endswith((".", "?", "!")) for t in texts):
            dropped["figure-label"] += len(lines)
            blocks[i] = []
            continue
        if max(l["size"] for l in lines) <= body - SUBSIZE_DELTA:
            dropped["table-or-figure-content"] += len(lines)
            blocks[i] = []

    for lines in blocks:
        for line in lines:
            text = line["text"]
            if FIG_CAPTION_RE.match(text) or TABLE_CAPTION_RE.match(text) or \
                    FIGURE_TAIL_RE.match(text):
                dropped["caption-line"] += 1
                continue
            if PANEL_LETTER_RE.match(text) or NUMBER_ONLY_RE.match(text):
                dropped["axis-or-page-number"] += 1
                continue
            kept.append(line)
    return kept, dropped


def body_size(lines):
    """Body font size, weighted by characters: counting lines instead lets a
    heading-heavy page (or a page of short caption fragments) outvote the text."""
    sizes = Counter()
    for line in lines:
        if line["text"]:
            sizes[round(line["size"], 1)] += len(line["text"])
    return sizes.most_common(1)[0][0] if sizes else 10.0


def is_heading(line, body):
    """A heading is set larger than the body, or semibold at body size.

    Callouts (▲ CRITICAL STEP, ● TIMING) are semibold too but belong to a step,
    so they are excluded; so are table and figure captions, which are dropped
    before this runs. So is "(B) Growing 2D organoids for long-term live
    imaging" — an option label inside a step, whose (i), (ii) ... sub-items
    would otherwise be orphaned into a step-less block — and "Option C: ...",
    which subdivides a procedure instead of opening one."""
    text = line["text"]
    if CALLOUT_RE.match(text) or SUBITEM_RE.match(text) or STEP_BARE_RE.match(text) \
            or STEP_INLINE_RE.match(text) or OPTION_LABEL_RE.match(text):
        return False
    if line["size"] + SIZE_EPSILON >= body + HEADING_SIZE_DELTA:
        return True
    return (line["size"] + SIZE_EPSILON >= body
            and BOLD_FONT_RE.search(line["font"] or "") is not None
            and len(text) <= BOLD_HEADING_MAX_CHARS)


def _section_end(pages, pno, idx):
    """Where the section opened at (pno, idx) ends: the next top-level heading
    from END_HEADINGS, or the document end."""
    for page_no in range(pno, len(pages)):
        lines = pages[page_no]
        body = body_size(lines)
        first = idx + 1 if page_no == pno else 0
        for i in range(first, len(lines)):
            if not is_heading(lines[i], body):
                continue
            if lines[i]["text"].rstrip(".").lower() in END_HEADINGS:
                return page_no, i
    return len(pages) - 1, len(pages[-1])


def _count_steps(pages, start_page, start_index, end_page, end_index):
    n = 0
    for pno in range(start_page, end_page + 1):
        lines = pages[pno]
        first = start_index + 1 if pno == start_page else 0
        last = end_index if pno == end_page else len(lines)
        for line in lines[first:last]:
            if STEP_BARE_RE.match(line["text"]) or STEP_INLINE_RE.match(line["text"]):
                n += 1
    return n


def _opens_on_step_one(pages, pno, idx, window=8):
    """Does the heading at (pno, idx) open onto a real step flow (step 1)?

    The Cu paper lists its four procedures by title in the Introduction and
    Experimental design, so "Procedure 1: ..." matches in several places; only
    the section that actually starts with step 1 is the procedure."""
    seen = 0
    for page_no in range(pno, len(pages)):
        lines = pages[page_no]
        first = idx + 1 if page_no == pno else 0
        for line in lines[first:]:
            text = line["text"]
            if STEP_BARE_RE.match(text) or STEP_INLINE_RE.match(text):
                return re.match(r"^1\.", text) is not None
            seen += 1
            if seen > window:
                return False
    return False


def find_procedure(doc, min_steps=10):
    """(start_page, start_index, end_page, end_index) of the Procedure section.

    Papers differ. Two carry a plain "Procedure" heading; the Cu paper has no
    umbrella heading and instead titles four numbered procedures — and it does
    use the bare word "Procedure" earlier, as a subsection of Experimental
    design, so a plain heading is only accepted when no numbered one exists.
    The dopamine paper (01422) titles its two procedures with the bare number
    ("Procedure 1" at 11.25 pt) while its outline and Materials lists repeat
    the same words at body size. A candidate must also open onto a real step
    flow, which rejects all of those look-alikes, the Introduction's procedure
    list and stray cross-references."""
    pages = [page_lines(doc[i])[0] for i in range(doc.page_count)]
    candidates = []
    for pno, lines in enumerate(pages):
        body = body_size(lines)
        for idx, line in enumerate(lines):
            if not is_heading(line, body):
                continue
            text = line["text"]
            if re.match(r"^Procedure\s+\d+\s*[:.]", text):
                candidates.append((0, pno, idx))
            elif re.fullmatch(r"Procedure\s+\d+", text):
                candidates.append((1, pno, idx))
            elif re.fullmatch(r"Procedure", text):
                candidates.append((2, pno, idx))
    for _, pno, idx in sorted(set(candidates)):
        if not _opens_on_step_one(pages, pno, idx):
            continue
        end_page, end_index = _section_end(pages, pno, idx)
        if _count_steps(pages, pno, idx, end_page, end_index) >= min_steps:
            return pno, idx, end_page, end_index
    return None


def collect_lines(doc, start_page, start_index, end_page, end_index):
    """Cleaned lines inside the Procedure window + the drop counters."""
    kept, dropped = [], Counter()
    for pno in range(start_page, end_page + 1):
        lines, page_dropped = page_lines(doc[pno])
        dropped.update(page_dropped)
        # The start heading itself is kept: for the Cu paper it is the only
        # place that says which of its four procedures this block belongs to.
        first = start_index if pno == start_page else 0
        last = end_index if pno == end_page else len(lines)
        kept.extend(lines[first:last])
    return kept, dropped


def segment(lines):
    """Group cleaned lines into steps, folding wrapped lines into their step and
    keeping sub-items ((i), (A)) on their own line.

    Returns items in order: {kind: heading|step|text}."""
    body = body_size(lines)
    items, current = [], None
    heading_since_step = False

    def step_count():
        return sum(1 for i in items if i["kind"] == "step")

    for line in lines:
        text = line["text"]
        if is_heading(line, body):
            current = None
            heading_since_step = True
            items.append({"kind": "heading", "text": text, "step": step_count()})
            continue
        bare = STEP_BARE_RE.match(text)
        inline = STEP_INLINE_RE.match(text)
        if bare:
            # "12." alone: the step text is the next line, which the fold below
            # appends to this newly opened step.
            current = {"kind": "step", "step": step_count(),
                       "num": int(bare.group(1)), "parts": []}
            items.append(current)
            heading_since_step = False
            continue
        if inline and not SUBITEM_RE.match(text):
            current = {"kind": "step", "step": step_count(),
                       "num": int(inline.group(1)), "parts": [inline.group(2)]}
            items.append(current)
            heading_since_step = False
            continue
        if TIMING_RE.match(text):
            # A stage timing describes the section, not the step before it.
            current = None
            items.append({"kind": "text", "text": text, "step": step_count()})
            continue
        if current is None:
            # Text before the first step: section notes, timings. A sub-item
            # here belongs to the step it follows — option groups like
            # "(A) Growing 3D organoids ... (iv) Let the organoids settle ..."
            # continue the step above them. Not after a heading though: the Cu
            # paper's Procedure 3 is written as options whose sub-items are a
            # procedure of their own, and folding them into Procedure 2's last
            # step merged two procedures into one block.
            last_step = next((i for i in reversed(items) if i["kind"] == "step"), None)
            if SUBITEM_RE.match(text) and last_step is not None and not heading_since_step:
                last_step["parts"].append("\n" + text)
                current = last_step
                continue
            items.append({"kind": "text", "text": text, "step": step_count()})
            continue
        if SUBITEM_RE.match(text):
            current["parts"].append("\n" + text)
        elif current["parts"]:
            current["parts"][-1] = _fold(current["parts"][-1], text)
        else:
            current["parts"].append(text)

    for item in items:
        if item["kind"] == "step":
            item["text"] = " ".join(p for p in item["parts"] if p).strip()
    return items


def _fold(previous, text):
    """Join a wrapped line onto the previous one.

    Nature Protocols justifies its columns, so a line can end mid-word
    ("incu-" / "bate"). The hyphen is kept: dropping it would corrupt genuine
    compounds ("3-day"), and a stray hyphen inside a rare word is harmless
    where a wrong value would not be."""
    if previous.endswith("-"):
        return previous + text
    return previous + " " + text


def _join_titles(previous, text):
    """Stack two titles when a stub block merges into its neighbour. A title
    that wraps mid-sentence ("... parenchymal nucleus" / "staining") continues
    the sentence and takes a space; separate labels take a dash."""
    if not previous:
        return text
    if previous.rstrip().endswith((".", ":", "?", "!")):
        return f"{previous} - {text}"
    return f"{previous} {text}"


GENERIC_HEADINGS = {"procedure", "stage", "protocol"}


def _is_top_level(text):
    """Headings that name the procedure itself rather than a place inside it."""
    return bool(re.match(r"^(?:Procedure|Stage)\s+\d+", text)) or \
        text.strip().lower() in GENERIC_HEADINGS


def _pick_title(stack):
    """Title for a stack of headings, and the ones left over.

    Papers stack headings at one location — the Cu paper marks every place
    three times over ("Procedure 1: ..." / "Tier 1, ..." / "Section 1: ...")
    and lists the sections of a tier up front. The FIRST non-generic heading
    names the block that follows, because a list of upcoming sections starts
    with the current one; the rest are written out as text so no wording from
    the paper is silently lost."""
    for heading in stack:
        if heading.strip().lower() not in GENERIC_HEADINGS:
            return heading, [h for h in stack if h != heading]
    return "", list(stack)


def lift_box_inserts(items):
    """Move a mid-section BOX's item run to the end so the interrupted section
    rejoins and the box stands as its own block.

    Nature Protocols boxes carry their own step numbering and float inside a
    section: the ipsc paper's BOX 1 (its own steps 1-12) sits between main
    steps 32 and 33, and the main numbering resumes with no new heading — left
    in place, the box and the section's tail land in one block under the box's
    title. The resumption is identified as the first step after the box that
    continues the last main step before it. A box written as prose (the TEMI
    paper's BOX 2, instrument parameters) interrupts the flow the same way and
    is lifted too: left in place, the steps that resume after it are filed
    under the box's title with a fragment of box text in front of them."""
    out = list(items)
    while True:
        start = next((i for i, it in enumerate(out)
                      if it["kind"] == "heading" and re.match(r"BOX\s*\d", it["text"], re.I)), None)
        if start is None:
            return out
        last_main = next((it["num"] for it in reversed(out[:start])
                          if it["kind"] == "step"), None)
        if last_main is None:
            return out        # box before any step: nothing interrupted
        resume = next((i for i, it in enumerate(out) if i > start and it["kind"] == "step"
                       and it["num"] == last_main + 1), None)
        if resume is None:
            return out        # no resumption (box at the end): already a clean block
        end = resume
        while end > start and out[end - 1]["kind"] == "heading":
            end -= 1          # a heading right before the resumption (the TEMI
                              # paper's "LC–MS analysis") names the resumed
                              # section, not the box, and stays in the flow
        if end <= start:
            # A box whose first step already continues the main numbering with
            # no prose in front of it: there is nothing to lift, and looping on
            # it would never change `out`.
            return out
        box_run = out[start:end]
        del out[start:end]
        out.extend(box_run)


def build_blocks(items, max_block_chars, min_block_steps, min_block_chars):
    """Split the step flow at heading boundaries into standalone protocol blocks.

    Each block keeps the top-level procedure/stage it belongs to in `scope`,
    because the troubleshooting table refers to steps as "5 in Procedure 1" —
    a block titled only "Section 2: ..." could not be matched by that
    reference. A stub block merges into its neighbour so no block is a lone
    step, and a block longer than the official protocol ceiling is cut at a
    step boundary."""
    blocks, stack, scope, warnings = [], [], "", []
    for item in items:
        if item["kind"] == "heading":
            if _is_top_level(item["text"]):
                scope = item["text"]
            stack.append(item["text"])
            continue
        text = item["text"].strip()
        if not text:
            continue
        if stack or not blocks:
            if stack:
                title, leftover = _pick_title(stack)
                stack = []
            else:
                title, leftover = "", []
            blocks.append({"title": title, "scope": scope, "steps": [],
                           "preamble": list(leftover)})
        if item["kind"] == "step":
            blocks[-1]["steps"].append((item["num"], text))
        else:
            blocks[-1]["preamble"].append(text)

    merged = []
    for block in blocks:
        if not block["steps"]:
            # A procedure with no numbered steps (the Cu paper's Procedure 3 is
            # written as options A/B/C). Kept and flagged rather than dropped:
            # whether an unnumbered procedure can carry a question that names a
            # step is the generator's decision, not the extractor's. Short ones
            # are figure residue ("cThin layer") that survived the block filters.
            block["numbered"] = False
            if len("\n".join(block["preamble"])) >= MIN_UNNUMBERED_CHARS:
                merged.append(block)
            continue
        block["numbered"] = True
        # A stub merges into its neighbour — but never across a scope boundary:
        # the Cu paper's Procedure 2 opens with a single step before its first
        # sub-heading, and merging that away hid the whole procedure.
        if merged and merged[-1]["numbered"] and len(block["steps"]) < min_block_steps \
                and merged[-1]["scope"] == block["scope"] \
                and not _exceeds(block, max_block_chars):
            previous = merged[-1]
            previous["steps"].extend(block["steps"])
            previous["preamble"].extend(block["preamble"])
            if block["title"]:
                previous["title"] = _join_titles(previous["title"], block["title"])
            continue
        merged.append(block)

    merged = _merge_tiny(merged, min_block_chars, min_block_steps)

    out = OrderedDict()
    for block in merged:
        for sub in _split_oversized(block, max_block_chars, warnings):
            key = f"Block{len(out) + 1}"
            text = _render_protocol(sub)
            steps = sub["steps"]
            out[key] = {
                "title": sub["title"],
                "scope": sub["scope"],
                "numbered": sub["numbered"],
                "step_range": [steps[0][0], steps[-1][0]] if steps else None,
                "n_steps": len(steps),
                "protocol": text,
            }
    for key, block in out.items():
        if not block["numbered"]:
            warnings.append(f"{key} ({block['title'][:48] or 'untitled'}) has no "
                            f"numbered steps; flagged numbered=false")
    return out, warnings


def _contiguous(first, second):
    """Does `second` carry on from `first`, step-number-wise?

    Reads the step lists rather than the rendered `step_range`, because the
    merges run before that field exists."""
    if not (first["steps"] and second["steps"]):
        return False
    return first["steps"][-1][0] + 1 == second["steps"][0][0]


def _merge_tiny(blocks, min_block_chars, min_block_steps):
    """Merge blocks below the official size floor (569 chars) into a neighbour.

    The Cu paper's Procedure 2 opens with a single step before its first
    sub-heading, and its Procedure 4 splits at an internal sub-heading into a
    two-step fragment plus the rest — either way a fragment of one or two steps
    cannot carry a troubleshooting question the way a whole procedure can.
    Only a contiguous neighbour inside the same procedure can absorb it:
    merging across a scope boundary would splice two procedures together."""
    def same_scope(first, second):
        return first["numbered"] == second["numbered"] and \
            first["scope"] == second["scope"]

    def too_small(block):
        return len(_render_protocol(block)) < min_block_chars or \
            len(block["steps"]) < min_block_steps

    tidied = []
    for index, block in enumerate(blocks):
        following = blocks[index + 1] if index + 1 < len(blocks) else None
        if following and block["numbered"] and too_small(block) and \
                same_scope(block, following) and _contiguous(block, following):
            following["steps"] = block["steps"] + following["steps"]
            following["preamble"] = block["preamble"] + following["preamble"]
            if block["title"]:
                following["title"] = _join_titles(block["title"], following["title"])
            continue
        tidied.append(block)

    out = []
    for block in tidied:
        if out and block["numbered"] and too_small(block) and \
                same_scope(out[-1], block) and _contiguous(out[-1], block):
            previous = out[-1]
            previous["steps"].extend(block["steps"])
            previous["preamble"].extend(block["preamble"])
            if block["title"]:
                previous["title"] = _join_titles(previous["title"], block["title"])
            continue
        out.append(block)
    return out


def _render_protocol(block):
    """The protocol field as the respondent will read it: title, then any
    section notes, then the numbered steps."""
    parts = []
    if block["title"]:
        parts.append(block["title"])
    if block["preamble"]:
        parts.append("\n".join(block["preamble"]))
    if block["steps"]:
        parts.append("\n".join(f"{num}. {body}" for num, body in block["steps"]))
    return "\n\n".join(parts).strip()


def _exceeds(block, max_chars):
    """Rendered length of a block, title and preamble included — the ceiling it
    is checked against (14188 chars) applies to the protocol field as exported."""
    text = "\n".join(f"{n}. {t}" for n, t in block["steps"])
    return len(text) + len(block.get("title") or "") + \
        sum(len(p) for p in block.get("preamble") or []) > max_chars


def _split_oversized(block, max_chars, warnings):
    """Cut a block longer than the official protocol ceiling at step boundaries."""
    if not block["steps"]:
        return [block]
    pieces, current = [], []
    for num, text in block["steps"]:
        current.append((num, text))
        if _exceeds({"steps": current}, max_chars) and len(current) > 1:
            pieces.append(current[:-1])
            current = [(num, text)]
    if current:
        pieces.append(current)
    for piece in pieces:
        if _exceeds({"steps": piece}, max_chars):
            warnings.append(f"step {piece[0][0]} alone is {len(piece[0][1])} chars — "
                            f"longer than the official protocol ceiling and not "
                            f"splittable at a step boundary")
    return [{"title": block["title"] if i == 0 else
             (f"{block['title']} (cont.)" if block["title"] else ""),
             "steps": piece, "preamble": block["preamble"] if i == 0 else [],
             "scope": block["scope"], "numbered": block["numbered"]}
            for i, piece in enumerate(pieces)]


def extract_troubleshooting(doc, end_page, span=3):
    """Rows of the Troubleshooting table(s) as dicts keyed by header text.

    PyMuPDF's table finder is used because rows wrap across pages and a text
    heuristic would splice one cell into its neighbour."""
    rows, headers = [], None
    for pno in range(end_page, min(end_page + 1 + span, doc.page_count)):
        try:
            tables = doc[pno].find_tables()
        except Exception as exc:                    # noqa: BLE001
            print(f"[extract] table finder failed on page {pno + 1}: {exc}")
            continue
        for table in tables:
            for row in table.extract():
                cells = [normalize_inline(c or "") for c in row]
                if not any(cells):
                    continue
                joined = " ".join(cells).lower()
                if "problem" in joined and ("solution" in joined or "reason" in joined):
                    headers = [c.lower() for c in cells]
                    continue
                if headers:
                    rows.append(dict(zip(headers, cells)))
    return rows


def _row_field(row, *prefixes):
    for key, value in row.items():
        if key.startswith(prefixes):
            return value
    return ""


def map_troubleshooting(rows, blocks):
    """Attach each row to the block whose step range covers its step reference.

    Step cells look like "5 in Procedure 1", "15A(iii) in Procedure 1", "19" or
    "(i) in Procedure 2" — so the first integer is the reference, and any
    "Procedure N" / "Stage N" mention disambiguates papers whose numbering
    restarts. Rows that cannot be mapped are reported rather than dropped.

    Returns (unmatched, incomplete): three table conventions are handled instead
    of silently losing rows, all of them observed in these papers. A row with an
    empty Problem cell continues the previous row's problem — Nature Protocols
    merges the cell when one problem has several causes, as in 18 of the
    VINE-seq paper's 32 rows — so it is appended to that entry. A row that
    names only a procedure ("Procedure 2") is a divider: the dopamine paper's
    table splits that way because its two procedures restart step numbering,
    and the label carries down so a bare "32" lands on the right procedure. And
    the Cu paper heads its last column "Possible solution" rather than
    "Solution", which the column lookup must accept. A row whose solution cell
    is still empty cannot ground anything and is counted as incomplete."""
    for block in blocks.values():
        block["troubleshooting"] = []

    entries = []
    scope_carry = ""
    for row in rows:
        problem = _row_field(row, "problem")
        solution = _row_field(row, "possible solution", "solution")
        reason = _row_field(row, "possible reason", "reason")
        step_cell = _row_field(row, "step")
        if not problem and re.fullmatch(r"(procedure|stage|section)\s+\d+",
                                        step_cell.strip(), re.I):
            # The dopamine paper's table is split by "Procedure 1"/"Procedure 2"
            # divider rows, because its two procedures restart step numbering;
            # the label carries down to the rows below it.
            scope_carry = step_cell.strip()
            continue
        if not problem and entries:
            last = entries[-1]
            if solution:
                last["solution"] = f"{last['solution']} {solution}".strip()
            if reason and reason not in last["reason"]:
                last["reason"] = f"{last['reason']} {reason}".strip()
            continue
        if not problem:
            continue
        entries.append({"step": step_cell, "span": scope_carry, "problem": problem,
                        "reason": reason, "solution": solution})

    unmatched, incomplete = [], 0
    for entry in entries:
        if not entry["solution"]:
            incomplete += 1
            continue
        step_cell = entry["step"]
        scope = re.search(r"(procedure|stage|section)\s*(\d+)", step_cell, re.I) or \
            re.search(r"(procedure|stage|section)\s*(\d+)", entry["span"], re.I)
        numbers = [int(n) for n in re.findall(r"\d+", step_cell)]
        target = None
        box_ref = re.search(r"box\s*(\d+)", step_cell, re.I)
        if box_ref:
            # "Box 1" cells reference the box's own step numbering, which
            # restarts at 1 and collides with the first main section's range —
            # match the box by its title, not by number.
            tag = f"box {box_ref.group(1)}"
            target = next((key for key, block in blocks.items()
                           if tag in f"{block['title']} {block.get('scope', '')}".lower()), None)
        else:
            for key, block in blocks.items():
                if not block.get("step_range"):
                    continue            # unnumbered procedure (options A/B/C)
                haystack = f"{block['title']} {block.get('scope', '')}".lower()
                if scope and f"{scope.group(1)} {scope.group(2)}".lower() not in haystack:
                    continue
                lo, hi = block["step_range"]
                if numbers and lo <= numbers[0] <= hi:
                    target = key
                    break
        record = {key: value for key, value in entry.items() if key != "span"}
        if target:
            blocks[target]["troubleshooting"].append(record)
        else:
            unmatched.append(record)
    return unmatched, incomplete


def paper_title(doc):
    """Largest-font text on page 1 — the article title."""
    lines = page_lines(doc[0])[0]
    return max(lines, key=lambda l: l["size"])["text"] if lines else ""


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pdf", required=True, help="Nature Protocols PDF")
    parser.add_argument("--out", required=True,
                        help="Output dir for protocol.json (usually the paper work dir)")
    parser.add_argument("--paper-id", default=None,
                        help="Slug used as paper_id (default: PDF filename stem)")
    parser.add_argument("--min-block-steps", type=int, default=3,
                        help="Blocks smaller than this merge into the previous one")
    parser.add_argument("--min-block-chars", type=int, default=800,
                        help="Blocks shorter than this (official floor is 569 chars) "
                             "merge into a contiguous neighbour in the same procedure")
    parser.add_argument("--max-block-chars", type=int, default=14000,
                        help="Official protocol ceiling (14188 chars); larger blocks "
                             "are cut at a step boundary")
    parser.add_argument("--doi", default="",
                        help="Article DOI url, recorded for provenance; needed "
                             "when the working copy is named paper.pdf (the DOI "
                             "lives in the original s41596-* filename otherwise)")
    parser.add_argument("--dump-text", action="store_true",
                        help="Also write procedure_clean.txt for eyeballing")
    args = parser.parse_args()

    doc = fitz.open(args.pdf)
    found = find_procedure(doc)
    if not found:
        raise SystemExit("ERROR: no Procedure section found — check the PDF.")
    start_page, start_index, end_page, end_index = found
    lines, dropped = collect_lines(doc, start_page, start_index, end_page, end_index)
    items = segment(lines)
    items = lift_box_inserts(items)
    blocks, warnings = build_blocks(items, args.max_block_chars,
                                    args.min_block_steps, args.min_block_chars)
    rows = extract_troubleshooting(doc, end_page)
    unmatched, incomplete_rows = map_troubleshooting(rows, blocks)

    doi = args.doi
    if not doi:
        # The DOI lives in the article filename, which a working copy usually
        # loses (the dirs hold "paper.pdf"); --doi is the reliable route.
        # The check character is over 0-9 and w,x,y,z (the previous [\dx] class
        # missed "-w" names such as s41596-026-01427-w).
        m = re.search(r"(s\d{5}-\d{3}-\d{5}-[0-9a-z])",
                      f"{os.path.basename(args.pdf)} {args.paper_id or ''}")
        if m:
            doi = f"https://doi.org/10.1038/{m.group(1)}"

    payload = {
        "paper_id": args.paper_id or os.path.splitext(os.path.basename(args.pdf))[0],
        "doi": doi,
        "title": paper_title(doc),
        "source_pdf": os.path.abspath(args.pdf),
        "procedure_pages": [start_page + 1, end_page + 1],
        "blocks": blocks,
        "troubleshooting_unmatched": unmatched,
        "extraction": {
            "n_steps": sum(b["n_steps"] for b in blocks.values()),
            "n_headings": sum(1 for i in items if i["kind"] == "heading"),
            "dropped_lines": dict(dropped),
            "troubleshooting_rows": len(rows),
            "troubleshooting_rows_incomplete": incomplete_rows,
            "warnings": warnings,
        },
    }
    os.makedirs(args.out, exist_ok=True)
    out_path = os.path.join(args.out, "protocol.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    if args.dump_text:
        header = f"{payload['title']}\n{payload['doi']}\n"
        with open(os.path.join(args.out, "procedure_clean.txt"), "w",
                  encoding="utf-8") as f:
            f.write(header + "\n" + "\n".join(l["text"] for l in lines))

    print(f"Procedure: pages {start_page + 1}-{end_page + 1}, "
          f"{payload['extraction']['n_steps']} steps, "
          f"{len(blocks)} block(s) -> {out_path}")
    print(f"Dropped: {dict(dropped)}")
    for key, block in blocks.items():
        steps = f"{block['step_range'][0]:>3}-{block['step_range'][1]:<3}" \
            if block["step_range"] else "  (unnumbered)"
        print(f"  {key:8} steps {steps:>12} "
              f"{len(block['protocol']):>6} chars  ts={len(block['troubleshooting'])}  "
              f"{block['title'][:60]}")
    for warning in warnings:
        print(f"WARNING: {warning}")
    if unmatched:
        print(f"Troubleshooting rows not mapped to a block: {len(unmatched)}")
    if incomplete_rows:
        print(f"Troubleshooting rows skipped as incomplete (no problem/solution "
              f"cell): {incomplete_rows}")


if __name__ == "__main__":
    main()
