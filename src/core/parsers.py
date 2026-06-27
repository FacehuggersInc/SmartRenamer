"""
core.parsers — Filename parsers for rename Modes 1-4 (fansub, One Pace,
simple numbered, and existing SxxExx style filenames).
"""

import re
from pathlib import Path

from .filesystem import clean_title, extract_quality


def parse_fansub(filename: str) -> dict:
    stem = Path(filename).stem
    stem = re.sub(r'^\[.*?\]', '', stem).strip(" _")
    m = re.search(r'(?:_-_|[-_ ])(\d{1,4})(?:v\d)?(?:\s|\(|_|$)', stem)
    if not m:
        m = re.search(r'(\d{1,4})(?:v\d)?$', stem)
    ep = int(m.group(1)) if m else None
    title_part = re.split(r'_-_|(?<!\d)-(?!\d)', stem)[0].strip(" _")
    title_part = re.sub(r'\[.*?\]', '', title_part)
    show_guess = clean_title(title_part)
    return {"ep": ep, "quality": extract_quality(filename), "show_guess": show_guess}

def parse_one_pace(filename: str) -> dict:
    stem = Path(filename).stem
    cleaned = re.sub(r'^\[.*?\]\[.*?\]\s*', '', stem)
    quality = extract_quality(filename)
    trans_m = re.search(r'\[(En[^\]]*)\]', filename, re.IGNORECASE)
    translation = trans_m.group(1) if trans_m else ""
    cleaned = re.sub(r'\[.*?\]', '', cleaned).strip()
    parts = cleaned.split()
    ep_num, arc_parts = None, []
    for part in parts:
        m = re.match(r'^(\d+)(?:[vV]\d+)?$', part)
        if m:
            ep_num = int(m.group(1))
        elif ep_num is None:
            arc_parts.append(part)
    return {"ep": ep_num, "arc": " ".join(arc_parts),
            "quality": quality, "translation": translation}

def parse_simple(filename: str) -> dict:
    nums = re.findall(r'\d+', Path(filename).stem)
    return {"ep": int(nums[0]) if nums else None}

def parse_sxxexx(filename: str) -> dict:
    m = re.search(r'[Ss](\d{1,2})[Ee](\d{1,3})', filename)
    if m:
        return {"season": int(m.group(1)), "ep": int(m.group(2))}
    return {"season": None, "ep": None}
