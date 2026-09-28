"""The user's disc golf rounds, from UDisc's scorecard export (CSV).

UDisc only shows stats in its phone app; its CSV export (the same file the
user's UDisc Analyzer app reads) holds every scorecard. The newest CSV in
data/udisc/ is read here, and the stats are computed in code so the chat
model only has to phrase them. Personal data: stays on this machine and
only ever goes to the local model.

The export has one row per player per round, plus a "Par" row per round:
PlayerName, CourseName, LayoutName, StartDate, EndDate, Total, +/-,
RoundRating, Hole1, Hole2, ...
"""

import csv
import io
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

HOLE_COLUMN = re.compile(r"^Hole(\d+)$")
ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


@dataclass
class Round:
    player: str  # as written on the scorecard
    course: str
    layout: str
    started: str  # as exported, e.g. "2026-09-14 1030"
    holes: list  # (hole number, strokes, par), only holes that were played
    layout_holes: int  # holes with a par on the layout
    rating: float = None

    @property
    def day(self):
        match = ISO_DATE.search(self.started)
        try:
            return date(*map(int, match.groups())) if match else None
        except ValueError:
            return None

    @property
    def strokes(self):
        return sum(s for _, s, _ in self.holes)

    @property
    def par(self):
        return sum(p for _, _, p in self.holes)

    @property
    def vs_par(self):
        return self.strokes - self.par

    @property
    def complete(self):
        return len(self.holes) >= self.layout_holes > 0


def _number(text):
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return 0


def parse(text):
    """All rounds in a UDisc CSV export, every player's."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    fields = reader.fieldnames or []
    hole_columns = sorted((int(m.group(1)), name) for name in fields if (m := HOLE_COLUMN.match(name)))
    rows = [{k: (v or "").strip() for k, v in row.items() if k} for row in reader]

    def round_key(row):
        return row.get("CourseName", ""), row.get("LayoutName", ""), row.get("StartDate", "")

    pars = {round_key(row): row for row in rows if row.get("PlayerName") == "Par"}
    rounds, seen = [], set()
    for row in rows:
        par_row = pars.get(round_key(row))
        if row.get("PlayerName") == "Par" or par_row is None:
            continue
        key = (row.get("PlayerName", "").lower(), *round_key(row))
        if key in seen:
            continue
        seen.add(key)
        holes = []
        for number, column in hole_columns:
            strokes, par = _number(row.get(column)), _number(par_row.get(column))
            if strokes > 0 and par > 0:
                holes.append((number, strokes, par))
        try:
            rating = float(row.get("RoundRating")) or None
        except (TypeError, ValueError):
            rating = None
        layout_holes = sum(1 for _, column in hole_columns if _number(par_row.get(column)) > 0)
        rounds.append(Round(row.get("PlayerName", ""), row.get("CourseName", ""), row.get("LayoutName", ""),
                            row.get("StartDate", ""), holes, layout_holes, rating))
    return rounds


@dataclass
class Scorecards:
    """The user's own rounds from one export."""

    rounds: list  # the user's rounds, oldest first
    player: str
    exported: date  # when the export file was saved
    all_courses: list = field(default_factory=list)

    def at(self, course=None, layout=None, start=None, end=None):
        return [r for r in self.rounds
                if (course is None or r.course == course) and (layout is None or r.layout == layout)
                and (start is None or (r.day and r.day >= start)) and (end is None or (r.day and r.day <= end))]


def the_user(rounds, configured=None):
    """Whose rounds these are: the configured UDisc name, or else the player
    on the most scorecards (the export is the user's own)."""
    names = Counter(r.player.lower() for r in rounds if r.player)
    if configured and configured.strip().lower() in names:
        return configured.strip().lower()
    return names.most_common(1)[0][0] if names else None


def scorecards_from(text, exported, configured_player=None):
    rounds = parse(text)
    player = the_user(rounds, configured_player)
    mine = sorted((r for r in rounds if r.player.lower() == player), key=lambda r: r.started)
    display = Counter(r.player for r in mine).most_common(1)[0][0] if mine else ""
    return Scorecards(mine, display, exported, sorted({r.course for r in mine}))


_cache = {}


def load(folder, configured_player=None):
    """Scorecards from the newest CSV in `folder`, or None when there's none."""
    files = sorted(Path(folder).glob("*.csv"), key=lambda p: p.stat().st_mtime) if Path(folder).is_dir() else []
    if not files:
        return None
    newest = files[-1]
    key = (str(newest), newest.stat().st_mtime, configured_player)
    if key not in _cache:
        _cache.clear()
        exported = datetime.fromtimestamp(newest.stat().st_mtime).date()
        _cache[key] = scorecards_from(newest.read_text(encoding="utf-8-sig"), exported, configured_player)
    return _cache[key]


# --- which course and layout a question is about ----------------------------------

# Words in course and layout names that don't identify one.
GENERIC = {"park", "parks", "disc", "golf", "course", "courses", "dgc", "the", "at", "of", "and", "in", "on",
           "county", "regional", "state", "city", "memorial", "recreation", "area", "center", "club", "community",
           "layout", "layouts", "tees", "tee", "pad", "pads", "baskets", "basket", "pins", "pin", "to", "holes",
           "hole", "my", "a", "18", "9", "27"}


def _words(text):
    return set(re.findall(r"[a-z0-9]+", text.lower()))


# Colors name layouts (and some courses); on their own they're weak evidence
# of which course a question is about.
COLORS = {"red", "blue", "white", "gold", "green", "black", "yellow", "orange", "purple", "silver"}


def _best_matches(question, names, weak=frozenset()):
    """The names sharing the most words with the question. Distinctive words
    count first; `weak` words only break ties, so in "the red layout at
    Pine Hollow" a course named Pine Hollow beats one with "Red" in its name."""
    asked = _words(question)
    scored = {}
    for name in names:
        hits = {w for w in _words(name) - GENERIC if len(w) >= 3 or w.isdigit()} & asked
        scored[name] = (len(hits - weak), len(hits & weak))
    top = max(scored.values(), default=(0, 0))
    return [name for name, score in scored.items() if score == top] if top != (0, 0) else []


def match_courses(question, scorecards):
    layout_words = set().union(*(_words(r.layout) for r in scorecards.rounds)) if scorecards.rounds else set()
    return _best_matches(question, scorecards.all_courses, COLORS | layout_words)


EVERY_LAYOUT = re.compile(r"\b(per|each|every|all|different|by)\s+(the\s+)?layouts?\b|\blayouts\b", re.I)


def match_layouts(question, layouts, course=""):
    """The layouts a question names, or [] for all of them. Words from the
    course's own name don't count (a layout called "Maple Hill Long" isn't
    named by "at Maple Hill"), and "per layout" or "all layouts" means all."""
    if EVERY_LAYOUT.search(question):
        return []
    asked = " ".join(sorted(_words(question) - _words(course)))
    return _best_matches(asked, layouts)


DISC_GOLF = re.compile(r"\b(disc ?golf|frisbee golf|u ?disc)\b", re.I)
GOLF_WORDS = re.compile(r"\b(play(ed|ing)?|rounds?|scores?|shoot|shot|layouts?|par|birdies?|bogeys?|aces?|holes?)\b", re.I)


MONTHS = "jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|june?|july?|aug(ust)?|sept?(ember)?|oct(ober)?|nov(ember)?|dec(ember)?"
TIME_WORDS = re.compile(
    rf"\b(today|yesterday|tonight|week|weekend|month|year|season|spring|summer|fall|autumn|winter|since|"
    rf"between|(mon|tues|wednes|thurs|fri|satur|sun)day|{MONTHS}|\d{{4}}|\d{{1,2}}/\d{{1,2}})\b", re.I)


def names_a_time(question):
    """The router sometimes adds dates to a question that names no time
    ("what's my best score at Maple Hill?"); for rounds, those would hide
    most of the history."""
    return bool(TIME_WORDS.search(question))


def mentions_disc_golf(question, scorecards):
    """Disc golf named outright, or one of the user's courses along with a golf word."""
    if DISC_GOLF.search(question):
        return True
    return bool(scorecards and GOLF_WORDS.search(question) and match_courses(question, scorecards))


# --- stats for the model -------------------------------------------------------------

HOLE_QUESTION = re.compile(r"\b(holes?|birdies?|bogeys?|aces?|hole in one)\b", re.I)
RECENT = 5
TOP_COURSES = 10


def _score(r):
    sign = "+" if r.vs_par > 0 else ""
    return f"{r.strokes} ({sign}{r.vs_par if r.vs_par else 'E'})"


def _rating(value):
    return f", rating {value:.0f}" if value else ""


def _average(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _span(rounds):
    days = [r.day for r in rounds if r.day]
    return f"{min(days)} to {max(days)}" if days else "dates unknown"


def layout_lines(layout, rounds, with_holes):
    complete = [r for r in rounds if r.complete]
    partial = len(rounds) - len(complete)
    par = complete[0].par if complete else None
    lines = [f'  Layout "{layout}"' + (f" ({complete[0].layout_holes} holes, par {par})" if complete else "") +
             f": {len(rounds)} rounds on this layout" + (f", {partial} of them unfinished (left out of best, worst, and average)"
                                          if partial else "")]
    if complete:
        best = min(complete, key=lambda r: (r.vs_par, r.started))
        worst = max(complete, key=lambda r: (r.vs_par, r.started))
        avg = _average([r.strokes for r in complete])
        avg_rating = _average([r.rating for r in complete])
        lines += [f"    Best (lowest): {_score(best)} on {best.day}{_rating(best.rating)}",
                  f"    Worst (highest): {_score(worst)} on {worst.day}",
                  f"    Average: {avg:.1f} ({avg - par:+.1f})" + (f", average rating {avg_rating:.0f}" if avg_rating else "")]
    recent = sorted(rounds, key=lambda r: r.started, reverse=True)[:RECENT]
    lines.append("    Most recent: " + "; ".join(
        f"{r.day} {_score(r)}" + ("" if r.complete else f" (unfinished, {len(r.holes)} holes)") for r in recent))
    if with_holes and complete:
        lines.append("    By hole (average strokes vs par; birdie-or-better and bogey-or-worse share):")
        for number in sorted({n for r in complete for n, _, _ in r.holes}):
            played = [(s, p) for r in complete for n, s, p in r.holes if n == number]
            diffs = [s - p for s, p in played]
            aces = sum(1 for s, _ in played if s == 1)
            lines.append(f"      Hole {number} (par {played[0][1]}): {_average([s for s, _ in played]):.2f} "
                         f"({_average(diffs):+.2f}), birdie {100 * sum(d < 0 for d in diffs) / len(diffs):.0f}%, "
                         f"bogey+ {100 * sum(d > 0 for d in diffs) / len(diffs):.0f}%" + (f", aces {aces}" if aces else ""))
    return lines


def course_lines(course, rounds, question, layouts):
    lines = [f"{course}, all layouts together: {len(rounds)} rounds, {_span(rounds)}"]
    by_layout = Counter(r.layout for r in rounds)
    chosen = layouts or [name for name, _ in by_layout.most_common()]
    complete = [r for r in rounds if r.complete and r.layout in chosen]
    if len(chosen) > 1 and complete:
        # Compared in code, so the model needn't compare layouts itself.
        best = min(complete, key=lambda r: (r.vs_par, r.started))
        lines.append(f"  Best round of these layouts (lowest vs par): {_score(best)} on {best.layout} "
                     f"on {best.day}{_rating(best.rating)}")
    for layout in chosen:
        lines += layout_lines(layout, [r for r in rounds if r.layout == layout],
                              bool(HOLE_QUESTION.search(question)) and len(chosen) == 1)
    return lines


def overview_lines(rounds):
    complete = [r for r in rounds if r.complete]
    rated = [r for r in rounds if r.rating]
    partial = len(rounds) - len(complete)
    lines = [f"All courses: {len(rounds)} rounds, {_span(rounds)}, at {len({r.course for r in rounds})} courses"
             + (f"; {partial} unfinished" if partial else "")]
    if rated:
        best = max(rated, key=lambda r: r.rating)
        lines.append(f"  Best rating (highest): {best.rating:.0f} at {best.course} ({best.layout}) on {best.day}")
        lines.append(f"  Average rating: {_average([r.rating for r in rated]):.0f}")
    if complete:
        best = min(complete, key=lambda r: r.vs_par)
        lines.append(f"  Best score (lowest vs par): {_score(best)} at {best.course} ({best.layout}) on {best.day}")
    lines.append("  Most played: " + "; ".join(
        f"{course} {count}" for course, count in Counter(r.course for r in rounds).most_common(TOP_COURSES)))
    lines.append("  Most recent: " + "; ".join(
        f"{r.day} {r.course} ({r.layout}) {_score(r)}{_rating(r.rating)}"
        + ("" if r.complete else f" (unfinished, {len(r.holes)} holes)") for r in sorted(rounds, key=lambda r: r.started, reverse=True)[:RECENT]))
    return lines


def stats_section(scorecards, question, start=None, end=None):
    """What the model is given for a disc golf question: the courses and
    layouts it names, or an overview. Also returns the courses used."""
    header = (f"Disc golf, from the user's UDisc scorecards ({scorecards.player}'s rounds; export saved "
              f"{scorecards.exported}, so later rounds aren't included). Scores are strokes with +/- par. "
              f"As in all golf, lower is better: the best score is the fewest strokes, most under par "
              f"(-3 beats +2). Round ratings are the opposite: higher is better. All numbers are already "
              f"computed, so use them as given.")
    rounds = scorecards.at(start=start, end=end)
    if start:
        header += f"\nOnly rounds from {start} to {end}."
    if not rounds:
        return header + "\nNo rounds in that time.", []
    courses = [c for c in match_courses(question, scorecards) if any(r.course == c for r in rounds)]
    if not courses:
        return "\n".join([header, *overview_lines(rounds)]), []
    lines = [header]
    for course in courses:
        at_course = [r for r in rounds if r.course == course]
        layouts = match_layouts(question, sorted({r.layout for r in at_course}), course)
        lines += course_lines(course, at_course, question, layouts)
    return "\n".join(lines), courses
