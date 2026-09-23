"""
Identify.py
===========

Extract risk-related sections from one or more PDF annual reports using LOCAL
heading formatting.

Core rule
---------
1. Detect candidate headings from PDF formatting ONLY.
2. If a detected heading contains one of these roots:
       risk
       uncertaint
       outlook
   then START keeping text.
3. Use THAT matched heading's own font size as the local reference.
4. Keep all following text until the next detected heading whose font size is
   approximately the same as, or larger than, the matched starting heading.
5. Stop there and evaluate that new heading independently.
6. Remove very short extracted sections using a minimum word-count filter.

Important
---------
The presence of risk / uncertainty / outlook does NOT make a line a heading.

First:
    PDF formatting -> heading or body text

Then:
    heading text -> risk / uncertainty / outlook match

There is NO global Level 1 / Level 2 / Level 3 hierarchy.
Each matched heading defines its own local stopping threshold.

No LLM is used.
The original PDF wording is preserved.

Running the script without arguments processes every PDF in this folder.
Alternatively, pass one or more PDF filenames to process only those files.
"""


# ============================================================
# IMPORTS
# ============================================================

import os
import re
import csv
import json
import sys

from collections import Counter

import fitz  # PyMuPDF


# ============================================================
# SETTINGS
# ============================================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


# ------------------------------------------------------------
# INPUT PDFS
# ------------------------------------------------------------

# No arguments: process every PDF stored beside this script.
# With arguments: process only the listed PDF files.


# ------------------------------------------------------------
# OUTPUT DIRECTORY
# ------------------------------------------------------------

OUTPUT_DIR = os.path.join(
    SCRIPT_DIR,
    "risk_section_output_local"
)

os.makedirs(
    OUTPUT_DIR,
    exist_ok=True
)


def find_input_pdfs():
    """Return requested PDFs, or every PDF stored beside this script."""

    requested = sys.argv[1:]

    if requested:

        pdf_paths = [
            os.path.abspath(
                path
                if os.path.isabs(path)
                else os.path.join(SCRIPT_DIR, path)
            )
            for path in requested
        ]

    else:

        pdf_paths = sorted(
            os.path.join(SCRIPT_DIR, filename)
            for filename in os.listdir(SCRIPT_DIR)
            if filename.lower().endswith(".pdf")
        )

    if not pdf_paths:

        raise FileNotFoundError(
            f"No PDF files found in:\n{SCRIPT_DIR}"
        )

    for pdf_path in pdf_paths:

        if not pdf_path.lower().endswith(".pdf"):

            raise ValueError(
                f"Input is not a PDF:\n{pdf_path}"
            )

        if not os.path.isfile(pdf_path):

            raise FileNotFoundError(
                f"PDF not found:\n{pdf_path}"
            )

    return pdf_paths


# ------------------------------------------------------------
# HEADER ROOTS
# ------------------------------------------------------------

HEADER_ROOTS = (
    "risk",
    "uncertainty",
    "outlook",
)


# ------------------------------------------------------------
# LOCAL STOPPING TOLERANCE
#
# Example:
#
# Start heading = 14.0 pt
#
# A later heading >= 13.5 pt stops the section.
# ------------------------------------------------------------

FONT_SIZE_TOLERANCE = 0.50


# ------------------------------------------------------------
# HEADING DETECTION
# ------------------------------------------------------------

MIN_HEADING_FONT_INCREASE = 0.50

MAX_HEADING_CHARS = 220

MAX_HEADING_WORDS = 25


# ------------------------------------------------------------
# BOLD DETECTION
#
# At least 70% of the actual text characters in the line
# must be bold before the complete line is considered bold.
#
# This prevents one small bold span from making an ordinary
# body-text sentence look like a heading.
# ------------------------------------------------------------

MIN_BOLD_RATIO = 0.70


# ------------------------------------------------------------
# MINIMUM SECTION LENGTH
# ------------------------------------------------------------

MIN_SECTION_WORDS = 50


# ============================================================
# TEXT HELPERS
# ============================================================

def clean_text(text: str) -> str:
    """
    Light cleaning only.
    The original wording is preserved.
    """

    text = text.replace(
        "\u00ad",
        ""
    )

    text = re.sub(
        r"[ \t]+",
        " ",
        text
    )

    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text
    )

    return text.strip()


def heading_matches(text: str) -> bool:
    """
    Check whether an ALREADY DETECTED heading contains:

        risk
        uncertaint
        outlook

    This function does NOT determine whether something is
    a heading.
    """

    text = text.lower()

    return any(
        root in text
        for root in HEADER_ROOTS
    )


def count_words(text: str) -> int:
    """
    Count words in extracted section.
    """

    return len(
        re.findall(
            r"\b\w+\b",
            text,
            flags=re.UNICODE,
        )
    )


# ============================================================
# ESTIMATE BODY FONT SIZE
# ============================================================

def estimate_body_font_size(document) -> float:
    """
    Estimate normal body font size separately for each PDF.

    The most frequently occurring font size among reasonably
    long text spans is treated as the normal body-text size.
    """

    sizes = []

    for page in document:

        page_dict = page.get_text(
            "dict"
        )

        for block in page_dict.get(
            "blocks",
            []
        ):

            if "lines" not in block:

                continue

            for line in block["lines"]:

                for span in line.get(
                    "spans",
                    []
                ):

                    text = span.get(
                        "text",
                        ""
                    ).strip()

                    # Longer spans are more likely to represent
                    # normal body text.
                    if len(text) >= 25:

                        size = round(
                            float(
                                span.get(
                                    "size",
                                    10.0
                                )
                            ),
                            1
                        )

                        sizes.append(
                            size
                        )

    if not sizes:

        return 10.0

    return Counter(
        sizes
    ).most_common(1)[0][0]


# ============================================================
# EXTRACT INFORMATION FROM ONE PDF LINE
# ============================================================

def line_information(
    line,
    body_font_size
):
    """
    Convert one PDF line into:

        text
        font_size
        bold_ratio
        is_bold

    Important
    ---------
    Font size and boldness are weighted by the amount of text
    contained in each span.

    Therefore:

        one tiny bold span
        !=
        whole line is bold
    """

    text_parts = []

    weighted_font_total = 0.0

    total_chars = 0

    bold_chars = 0


    # --------------------------------------------------------
    # LOOP THROUGH SPANS IN THIS LINE
    # --------------------------------------------------------

    for span in line.get(
        "spans",
        []
    ):

        text = span.get(
            "text",
            ""
        ).strip()

        if not text:

            continue

        text_parts.append(
            text
        )

        # ----------------------------------------------------
        # NUMBER OF CHARACTERS IN THIS SPAN
        # ----------------------------------------------------

        char_count = len(
            re.findall(
                r"\w",
                text,
                flags=re.UNICODE,
            )
        )

        if char_count == 0:

            char_count = len(
                text
            )

        total_chars += (
            char_count
        )

        # ----------------------------------------------------
        # FONT SIZE
        #
        # Weighted by number of characters.
        # ----------------------------------------------------

        span_font_size = float(
            span.get(
                "size",
                body_font_size
            )
        )

        weighted_font_total += (
            span_font_size
            *
            char_count
        )

        # ----------------------------------------------------
        # BOLD
        # ----------------------------------------------------

        font_name = (
            span.get(
                "font",
                ""
            )
            .lower()
        )

        span_is_bold = (

            "bold" in font_name

            or

            "semibold" in font_name

            or

            "demibold" in font_name

            or

            "black" in font_name

            or

            "heavy" in font_name

        )

        if span_is_bold:

            bold_chars += (
                char_count
            )


    # --------------------------------------------------------
    # COMBINE LINE TEXT
    # --------------------------------------------------------

    text = clean_text(
        " ".join(
            text_parts
        )
    )

    if not text:

        return None


    # --------------------------------------------------------
    # WEIGHTED FONT SIZE
    # --------------------------------------------------------

    if total_chars > 0:

        font_size = (
            weighted_font_total
            /
            total_chars
        )

    else:

        font_size = (
            body_font_size
        )


    # --------------------------------------------------------
    # BOLD RATIO
    #
    # Example:
    #
    # 100 characters
    # 90 bold
    #
    # bold_ratio = 0.90
    # --------------------------------------------------------

    if total_chars > 0:

        bold_ratio = (
            bold_chars
            /
            total_chars
        )

    else:

        bold_ratio = 0.0


    # --------------------------------------------------------
    # COMPLETE LINE CONSIDERED BOLD?
    # --------------------------------------------------------

    is_bold = (

        bold_ratio

        >=

        MIN_BOLD_RATIO

    )


    return {

        "text":
            text,

        "font_size":
            font_size,

        "bold_ratio":
            bold_ratio,

        "is_bold":
            is_bold,

    }


# ============================================================
# HEADING DETECTION
# ============================================================

def looks_like_heading(
    text,
    font_size,
    body_font_size,
    is_bold,
    bold_ratio
):
    """
    Determine whether a PDF line is structurally a heading.

    IMPORTANT:

    risk / uncertaint / outlook play NO ROLE here.

    We first determine:

        HEADING
            vs
        BODY TEXT

    using formatting.

    Only afterwards do we check the heading keywords.
    """

    text = clean_text(
        text
    )

    words = text.split()


    # ========================================================
    # BASIC EXCLUSIONS
    # ========================================================

    if not text:

        return False


    # --------------------------------------------------------
    # Too long
    # --------------------------------------------------------

    if len(text) > MAX_HEADING_CHARS:

        return False


    if len(words) > MAX_HEADING_WORDS:

        return False


    # --------------------------------------------------------
    # Pure page number
    # --------------------------------------------------------

    if re.fullmatch(
        r"\d+",
        text
    ):

        return False


    # --------------------------------------------------------
    # Must contain actual letters
    # --------------------------------------------------------

    alpha_count = sum(

        char.isalpha()

        for char in text

    )


    if alpha_count < 3:

        return False


    # ========================================================
    # SIGNAL 1:
    # LARGER FONT
    # ========================================================

    larger_font = (

        font_size

        >=

        body_font_size
        +
        MIN_HEADING_FONT_INCREASE

    )


    # ========================================================
    # SIGNAL 2:
    # GENUINELY BOLD
    #
    # Allows headings with same font size as body text.
    #
    # But most of the line must actually be bold.
    # ========================================================

    genuinely_bold = (

        is_bold

        and

        bold_ratio
        >=
        MIN_BOLD_RATIO

        and

        font_size
        >=
        body_font_size
        -
        0.20

    )


    # ========================================================
    # SIGNAL 3:
    # NUMBERED HEADING
    #
    # Examples:
    #
    # 3 Risk Management
    # 3.1 Credit Risk
    # III. Risk Factors
    # A. Market Risk
    #
    # Numbering alone is NOT enough.
    # ========================================================

    numbered_pattern = bool(

        re.match(

            r"^\s*"

            r"(?:"

            r"\d+(?:\.\d+)*"

            r"|"

            r"[IVXLCDM]+\.?"

            r"|"

            r"[A-Z]\."

            r")"

            r"\s+\S+",

            text,

            flags=re.IGNORECASE,

        )

    )


    numbered = (

        numbered_pattern

        and

        (
            genuinely_bold
            or
            larger_font
        )

    )


    # ========================================================
    # SIGNAL 4:
    # UPPERCASE HEADING
    # ========================================================

    letters = [

        char

        for char in text

        if char.isalpha()

    ]


    uppercase = False


    if len(letters) >= 5:

        uppercase_ratio = (

            sum(

                char.isupper()

                for char in letters

            )

            /

            len(letters)

        )


        uppercase = (

            uppercase_ratio
            >=
            0.85

            and

            (
                genuinely_bold
                or
                larger_font
            )

        )


    # ========================================================
    # ADJUSTMENT 1:
    # SENTENCE-LIKE FALSE POSITIVE SAFEGUARD
    #
    # Some PDF layouts render ordinary body-text fragments
    # slightly larger than the estimated body font.
    #
    # A line that looks like a normal sentence should not
    # become a heading solely because its font is larger.
    #
    # Genuine bold, numbered and uppercase headings are
    # preserved.
    # ========================================================

    sentence_like = (

        len(words) >= 6

        and

        text.endswith(
            (".", ";")
        )

        and

        not genuinely_bold

        and

        not numbered

        and

        not uppercase

    )


    if sentence_like:

        return False


    # ========================================================
    # FINAL HEADING DECISION
    # ========================================================

    return (

        larger_font

        or

        genuinely_bold

        or

        numbered

        or

        uppercase

    )


# ============================================================
# EXTRACT ORDERED LINES FROM PDF
# ============================================================

def extract_lines(
    pdf_path
):
    """
    Read complete PDF and return ordered lines.

    Each line contains:

        page
        text
        font_size
        bold_ratio
        is_bold
        is_heading
    """

    if not os.path.exists(
        pdf_path
    ):

        raise FileNotFoundError(

            f"PDF not found:\n"
            f"{pdf_path}"

        )


    document = fitz.open(
        pdf_path
    )


    body_font_size = (

        estimate_body_font_size(
            document
        )

    )


    lines_out = []


    # ========================================================
    # LOOP THROUGH PDF
    # ========================================================

    for page_number, page in enumerate(
        document,
        start=1
    ):

        page_dict = page.get_text(
            "dict",
            sort=True
        )


        for block in page_dict.get(
            "blocks",
            []
        ):

            if "lines" not in block:

                continue


            for raw_line in block[
                "lines"
            ]:

                info = line_information(

                    raw_line,

                    body_font_size

                )


                if info is None:

                    continue


                info["page"] = (
                    page_number
                )


                # --------------------------------------------
                # FIRST:
                # Is this structurally a heading?
                # --------------------------------------------

                info["is_heading"] = (

                    looks_like_heading(

                        info["text"],

                        info["font_size"],

                        body_font_size,

                        info["is_bold"],

                        info["bold_ratio"],

                    )

                )


                lines_out.append(
                    info
                )


    document.close()


    return (
        lines_out,
        body_font_size
    )


# ============================================================
# LOCAL SECTION EXTRACTION
# ============================================================

def extract_matching_sections(
    lines
):
    """
    Extract risk / uncertainty / outlook sections.

    STEP 1
    ------
    Is this structurally a heading?

    STEP 2
    ------
    Does the heading contain:

        risk
        uncertaint
        outlook

    STEP 3
    ------
    If yes -> START.

    Page 1 cannot start an extracted section.

    STEP 4
    ------
    Use the matched heading's own font size as the local
    stopping reference.

    STEP 5
    ------
    Stop at the next structural heading with approximately
    the same or larger font size.

    STEP 6
    ------
    Apply minimum word-count filter.
    """

    sections = []

    review_candidates = []

    i = 0

    section_id = 1


    while i < len(
        lines
    ):

        line = lines[i]


        # ====================================================
        # START CONDITION
        #
        # ADJUSTMENT 2:
        # Page 1 cannot start an extracted section.
        #
        # This prevents cover-page titles such as "Outlook"
        # from causing the complete annual report to be
        # extracted as one section.
        # ====================================================

        if (

            line["page"] == 1

            or

            not line[
                "is_heading"
            ]

            or

            not heading_matches(
                line[
                    "text"
                ]
            )

        ):

            i += 1

            continue


        # ====================================================
        # START SECTION
        # ====================================================

        start_font_size = (
            line[
                "font_size"
            ]
        )


        start_bold_ratio = (
            line[
                "bold_ratio"
            ]
        )


        start_page = (
            line[
                "page"
            ]
        )


        start_heading = (
            line[
                "text"
            ]
        )


        kept_lines = [
            line
        ]


        j = i + 1


        # ====================================================
        # LOCAL STOPPING RULE
        # ====================================================

        while j < len(
            lines
        ):

            candidate = (
                lines[j]
            )


            # ------------------------------------------------
            # Only structural headings can stop the section.
            # ------------------------------------------------

            if candidate[
                "is_heading"
            ]:


                # --------------------------------------------
                # Same size or larger than starting heading
                # --------------------------------------------

                if (

                    candidate[
                        "font_size"
                    ]

                    >=

                    start_font_size
                    -
                    FONT_SIZE_TOLERANCE

                ):

                    break


            kept_lines.append(
                candidate
            )


            j += 1


        # ====================================================
        # COMBINE ORIGINAL TEXT
        # ====================================================

        section_text = clean_text(

            "\n".join(

                item[
                    "text"
                ]

                for item
                in kept_lines

            )

        )


        # ====================================================
        # WORD COUNT
        # ====================================================

        section_word_count = count_words(
            section_text
        )


        # ====================================================
        # MINIMUM SECTION LENGTH AND REVIEW RECORD
        # ====================================================

        included_in_analysis = (

            section_word_count

            >=

            MIN_SECTION_WORDS

        )


        analysis_section_id = (

            section_id

            if included_in_analysis

            else None

        )


        stopping_heading = (

            lines[j]

            if j < len(lines)

            else None

        )


        review_candidates.append(

            {

                "candidate_id":
                    len(review_candidates) + 1,

                "included_in_analysis":
                    included_in_analysis,

                "analysis_section_id":
                    analysis_section_id,

                "page_start":
                    start_page,

                "page_end":
                    kept_lines[-1][
                        "page"
                    ],

                "heading":
                    start_heading,

                "heading_font_size":
                    round(
                        start_font_size,
                        2
                    ),

                "heading_bold_ratio":
                    round(
                        start_bold_ratio,
                        2
                    ),

                "word_count":
                    section_word_count,

                "extracted_text":
                    section_text,

                "context_before": [

                    {

                        "page":
                            item[
                                "page"
                            ],

                        "text":
                            item[
                                "text"
                            ],

                    }

                    for item
                    in lines[
                        max(0, i - 3):i
                    ]

                ],

                "stopping_heading": (

                    {

                        "page":
                            stopping_heading[
                                "page"
                            ],

                        "text":
                            stopping_heading[
                                "text"
                            ],

                        "font_size":
                            round(
                                stopping_heading[
                                    "font_size"
                                ],
                                2
                            ),

                        "bold_ratio":
                            round(
                                stopping_heading[
                                    "bold_ratio"
                                ],
                                2
                            ),

                    }

                    if stopping_heading

                    else None

                ),

                "context_after_stop": [

                    {

                        "page":
                            item[
                                "page"
                            ],

                        "text":
                            item[
                                "text"
                            ],

                    }

                    for item
                    in lines[
                        j:min(
                            len(lines),
                            j + 4
                        )
                    ]

                ],

            }

        )


        if included_in_analysis:

            sections.append(

                {

                    "section_id":
                        section_id,

                    "page_start":
                        start_page,

                    "page_end":
                        kept_lines[-1][
                            "page"
                        ],

                    "heading_font_size":
                        round(
                            start_font_size,
                            2
                        ),

                    "heading_bold_ratio":
                        round(
                            start_bold_ratio,
                            2
                        ),

                    "heading":
                        start_heading,

                    "word_count":
                        section_word_count,

                    "text":
                        section_text,

                }

            )


            section_id += 1


        # ====================================================
        # IMPORTANT:
        #
        # Evaluate the stopping heading independently.
        # ====================================================

        i = j


    return (
        sections,
        review_candidates
    )


# ============================================================
# SAVE ANALYSIS TEXT
# ============================================================

def save_analysis_text(
    path,
    sections
):
    """
    Save only the heading and extracted text used in the analysis.
    """

    with open(
        path,
        "w",
        encoding="utf-8"
    ) as file:

        for index, section in enumerate(
            sections
        ):

            if index:

                file.write(
                    "\n\n"
                )

            file.write(

                section[
                    "text"
                ].strip()

            )


# ============================================================
# SAVE CLAUDE REVIEW PACKAGE
# ============================================================

def save_claude_review(
    path,
    pdf_path,
    body_font_size,
    review_candidates
):
    """
    Save accepted and rejected candidates with boundary context
    for review.
    """

    review_package = {

        "source_pdf":
            os.path.abspath(
                pdf_path
            ),

        "review_purpose":
            (
                "Check whether each candidate starts at a genuine heading, stops at "
                "the correct comparable-or-larger heading, and was correctly included "
                "or excluded by the minimum word threshold."
            ),

        "review_questions": [

            "Is the candidate heading a genuine structural heading?",

            "Does the extracted text start and stop at the correct boundaries?",

            "Should a rejected short candidate have been included?",

            "Does the context suggest a multiline heading was split?",

        ],

        "settings": {

            "header_roots":
                list(
                    HEADER_ROOTS
                ),

            "font_size_tolerance":
                FONT_SIZE_TOLERANCE,

            "minimum_section_words":
                MIN_SECTION_WORDS,

            "estimated_body_font_size":
                round(
                    body_font_size,
                    2
                ),

        },

        "candidates":
            review_candidates,

    }


    with open(
        path,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            review_package,
            file,
            ensure_ascii=False,
            indent=2
        )


# ============================================================
# HEADING DIAGNOSTICS
# ============================================================

def save_heading_diagnostics(
    path,
    lines
):
    """
    Save every line classified as a structural heading.

    Useful for diagnosing false positives.
    """

    headings = [

        line

        for line
        in lines

        if line[
            "is_heading"
        ]

    ]


    fields = [

        "page",

        "font_size",

        "bold_ratio",

        "is_bold",

        "keyword_match",

        "text",

    ]


    with open(
        path,
        "w",
        newline="",
        encoding="utf-8"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=fields
        )


        writer.writeheader()


        for heading in headings:

            writer.writerow(

                {

                    "page":
                        heading[
                            "page"
                        ],

                    "font_size":
                        round(
                            heading[
                                "font_size"
                            ],
                            2
                        ),

                    "bold_ratio":
                        round(
                            heading[
                                "bold_ratio"
                            ],
                            2
                        ),

                    "is_bold":
                        heading[
                            "is_bold"
                        ],

                    "keyword_match":
                        heading_matches(
                            heading[
                                "text"
                            ]
                        ),

                    "text":
                        heading[
                            "text"
                        ],

                }

            )


# ============================================================
# MAIN
# ============================================================

def process_pdf(
    pdf_path
):
    """
    Process one PDF and write its three output files.
    """

    lines, body_font_size = extract_lines(
        pdf_path
    )


    sections, review_candidates = (

        extract_matching_sections(
            lines
        )

    )


    pdf_name = os.path.splitext(

        os.path.basename(
            pdf_path
        )

    )[0]


    headings_path = os.path.join(

        OUTPUT_DIR,

        f"{pdf_name}_heading_diagnostics_local.csv"

    )


    analysis_path = os.path.join(

        OUTPUT_DIR,

        f"analysis_text{pdf_name}.txt"

    )


    claude_path = os.path.join(

        OUTPUT_DIR,

        f"{pdf_name}_claude_review_local.json"

    )


    save_heading_diagnostics(
        headings_path,
        lines
    )


    save_analysis_text(
        analysis_path,
        sections
    )


    save_claude_review(

        claude_path,

        pdf_path,

        body_font_size,

        review_candidates,

    )


    return {

        "pdf":
            os.path.basename(
                pdf_path
            ),

        "lines":
            len(
                lines
            ),

        "headings":
            sum(

                1

                for line
                in lines

                if line[
                    "is_heading"
                ]

            ),

        "sections":
            len(
                sections
            ),

        "review_candidates":
            len(
                review_candidates
            ),

    }


def main():

    pdf_paths = find_input_pdfs()

    completed = []

    failed = []


    for pdf_path in pdf_paths:

        try:

            completed.append(

                process_pdf(
                    pdf_path
                )

            )

        except Exception as error:

            failed.append(

                {

                    "pdf":
                        os.path.basename(
                            pdf_path
                        ),

                    "error":
                        str(
                            error
                        ),

                }

            )


    summary_lines = [

        "",

        (
            f"Batch complete: "
            f"{len(completed)} of "
            f"{len(pdf_paths)} PDFs processed."
        ),

    ]


    summary_lines.extend(

        (
            f"  {item['pdf']}: "
            f"{item['sections']} analysis sections, "
            f"{item['review_candidates']} Claude candidates"
        )

        for item
        in completed

    )


    if failed:

        summary_lines.append(
            "Failed:"
        )


        summary_lines.extend(

            (
                f"  {item['pdf']}: "
                f"{item['error']}"
            )

            for item
            in failed

        )


    summary_lines.append(

        f"Output directory: "
        f"{OUTPUT_DIR}\n"

    )


    print(

        "\n".join(
            summary_lines
        )

    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()