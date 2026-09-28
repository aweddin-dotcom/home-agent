"""Disc golf questions, from an invented UDisc scorecard export."""

import os
from datetime import date
from zoneinfo import ZoneInfo

from services.discgolf.scorecards import (load, match_courses, mentions_disc_golf, parse, scorecards_from,
                                          stats_section)
from services.retrieval.ask import answer_prompt, format_sources, gather
from services.retrieval.router import named_ranges, validate

from .conftest import FIXTURES

TZ = ZoneInfo("America/New_York")
TODAY = date(2026, 9, 27)
EXPORT = (FIXTURES / "udisc_scorecards.csv").read_text(encoding="utf-8")
MAPLE = "Maple Hill Disc Golf Course"


def cards(player=None):
    return scorecards_from(EXPORT, date(2026, 9, 21), player)


# --- parsing ---------------------------------------------------------------------


def test_parse_skips_par_rows_and_duplicates():
    rounds = parse(EXPORT)
    assert all(r.player != "Par" for r in rounds)
    # The 2026-09-06 round is exported twice, with different capitalization.
    assert len([r for r in rounds if r.started == "2026-09-06 0800"]) == 1
    assert len(rounds) == 9


def test_round_totals_come_from_the_holes():
    first = next(r for r in parse(EXPORT) if r.player == "Pat Example")
    assert (first.strokes, first.par, first.vs_par, first.complete) == (14, 12, 2, True)
    assert first.day == date(2026, 5, 2) and first.rating == 810


def test_unfinished_rounds_are_marked():
    partial = next(r for r in parse(EXPORT) if r.started == "2026-09-20 1500")
    assert not partial.complete and len(partial.holes) == 2


def test_the_user_is_on_the_most_scorecards():
    assert cards().player == "Pat Example"
    assert len(cards().rounds) == 7


def test_a_configured_player_wins():
    assert cards("sam sample").player == "Sam Sample"
    assert cards("Nobody Here").player == "Pat Example"  # unknown name: fall back


def test_load_uses_the_newest_export(tmp_path):
    old, new = tmp_path / "old.csv", tmp_path / "new.csv"
    old.write_text(EXPORT.split("\n", 3)[0] + "\n", encoding="utf-8")  # header only
    new.write_text("﻿" + EXPORT, encoding="utf-8")  # with a byte order mark
    os.utime(old, (1_700_000_000, 1_700_000_000))
    os.utime(new, (1_800_000_000, 1_800_000_000))
    assert len(load(tmp_path).rounds) == 7
    assert load(tmp_path / "missing") is None


# --- matching --------------------------------------------------------------------


def test_course_named_by_its_distinctive_word():
    assert match_courses("how many times have I played maple hill?", cards()) == [MAPLE]
    assert match_courses("best score at riverside", cards()) == ["Riverside Park"]
    # "park" and "golf course" name no course on their own
    assert match_courses("what's the best disc golf course in the park?", cards()) == []


def test_disc_golf_mentions():
    assert mentions_disc_golf("how's my disc golf going?", None)
    assert mentions_disc_golf("what did I shoot at Maple Hill?", cards())
    assert not mentions_disc_golf("any emails from Maple Hill HOA?", cards())


# --- stats -----------------------------------------------------------------------


def test_course_and_layout_question():
    text, courses = stats_section(cards(), "how many times have I played maple hill and what's my best on red?")
    assert courses == [MAPLE]
    assert f"{MAPLE}, all layouts together: 5 rounds, 2026-05-02 to 2026-09-20" in text
    assert 'Layout "Red Tees" (4 holes, par 12): 4 rounds on this layout, 1 of them unfinished' in text
    assert "Best (lowest): 11 (-1) on 2026-07-19, rating 905" in text
    assert "Worst (highest): 14 (+2) on 2026-05-02" in text
    assert "Average: 12.7 (+0.7)" in text
    assert "Blue Tees" not in text  # only the layout asked about


def test_all_layouts_when_none_is_named():
    text, _ = stats_section(cards(), "how do I do at maple hill?")
    assert 'Layout "Red Tees"' in text and 'Layout "Blue Tees" (4 holes, par 14): 1 rounds on this layout' in text


def test_unfinished_round_is_never_the_best():
    text, _ = stats_section(cards(), "best score at riverside")
    assert "Best (lowest): 15 (+2) on 2025-10-11" in text
    assert "2026-08-23 1 (-2) (unfinished, 1 holes)" in text


def test_hole_stats_only_when_asked():
    assert "By hole" not in stats_section(cards(), "best score on maple hill red")[0]
    text, _ = stats_section(cards(), "which hole is my worst on maple hill red?")
    assert "Hole 1 (par 3): 3.00 (+0.00), birdie 33%, bogey+ 33%" in text
    assert "Hole 3 (par 3): 3.33 (+0.33)" in text


def test_overview_without_a_course():
    text, courses = stats_section(cards(), "how's my disc golf going?")
    assert courses == []
    assert "All courses: 7 rounds, 2025-10-11 to 2026-09-20, at 2 courses" in text
    assert "Best rating (highest): 905 at Maple Hill Disc Golf Course (Red Tees) on 2026-07-19" in text
    assert f"Most played: {MAPLE} 5; Riverside Park 2" in text
    assert "export saved 2026-09-21" in text


def test_dates_limit_the_rounds():
    text, _ = stats_section(cards(), "how did I play?", date(2026, 9, 1), date(2026, 9, 30))
    assert "Only rounds from 2026-09-01 to 2026-09-30." in text
    assert "All courses: 2 rounds" in text
    assert "No rounds in that time." in stats_section(cards(), "golf?", date(2024, 1, 1), date(2024, 12, 31))[0]


# --- routing and answering ---------------------------------------------------------


def test_router_accepts_disc_golf():
    r = validate('{"sources": ["disc_golf", "general"], "start_date": null, "end_date": null, '
                 '"calendar_keywords": []}')
    assert r.sources == ["disc_golf"]


def test_year_and_month_ranges():
    r = named_ranges(TODAY)
    assert r["last month"] == (date(2026, 8, 1), date(2026, 8, 31))
    assert r["this month"] == (date(2026, 9, 1), date(2026, 9, 30))
    assert r["this year"] == (date(2026, 1, 1), date(2026, 12, 31))
    assert r["last year"] == (date(2025, 1, 1), date(2025, 12, 31))
    assert named_ranges(date(2027, 1, 15))["last month"] == (date(2026, 12, 1), date(2026, 12, 31))


class RouterChat:
    def __init__(self, reply):
        self.reply = reply

    def complete(self, system, user, schema=None, temperature=None):
        return self.reply


class NoSearch:
    """Fails the test if email search runs."""

    def embed_query(self, text):
        raise AssertionError("email was searched")


def ask_with(route_reply, question):
    return gather(question, NoSearch(), None, RouterChat(route_reply), 5, [], TODAY, TZ, discgolf=cards)


def test_disc_golf_only_question_skips_email_and_calendar():
    context = ask_with('{"sources": ["disc_golf"], "start_date": null, "end_date": null, "calendar_keywords": []}',
                       "whats my best score on maple hill red?")
    assert context.sections[0].startswith("Disc golf, from the user's UDisc scorecards")
    assert len(context.sections) == 1
    assert "UDisc scorecards: Maple Hill Disc Golf Course; export saved 2026-09-21" in format_sources(context, TZ)
    system, user = answer_prompt("q", context, TODAY)
    assert "Disc golf stats come from the user's UDisc scorecards" in system


def test_course_name_catches_a_missed_route():
    context = ask_with('{"sources": ["general"], "start_date": null, "end_date": null, "calendar_keywords": []}',
                       "what did I shoot at riverside?")
    assert context.route.sources == ["disc_golf"]
    assert "Riverside Park, all layouts together: 2 rounds" in context.sections[0]


def test_no_export_says_where_it_goes():
    context = gather("how's my disc golf?", NoSearch(), None,
                     RouterChat('{"sources": ["disc_golf"], "start_date": null, "end_date": null, '
                                '"calendar_keywords": []}'), 5, [], TODAY, TZ, discgolf=lambda: None)
    assert "no UDisc scorecard export found" in context.sections[0]
    assert "no export found in data/udisc/" in format_sources(context, TZ)


def test_dates_the_question_doesnt_name_are_ignored():
    # The router gave dates for a question that names no time.
    context = ask_with('{"sources": ["disc_golf"], "start_date": "2026-09-27", "end_date": "2026-09-27", '
                       '"calendar_keywords": []}', "which hole gives me the most trouble on maple hill red?")
    assert "Only rounds from" not in context.sections[0]
    assert "By hole" in context.sections[0]
    assert context.route.start is None and "Searched: disc_golf\n" in format_sources(context, TZ) + "\n"


def test_dates_the_question_names_are_used():
    context = ask_with('{"sources": ["disc_golf"], "start_date": "2026-09-01", "end_date": "2026-09-30", '
                       '"calendar_keywords": []}', "how did my disc golf go this month?")
    assert "Only rounds from 2026-09-01 to 2026-09-30." in context.sections[0]


def test_overview_marks_unfinished_rounds():
    text, _ = stats_section(cards(), "how's my disc golf going?")
    assert "at 2 courses; 2 unfinished" in text
    assert "2026-09-20 Maple Hill Disc Golf Course (Red Tees) 5 (-1) (unfinished, 2 holes)" in text


TWO_REDS = """PlayerName,CourseName,LayoutName,StartDate,EndDate,Total,+/-,RoundRating,Hole1,Hole2
Par,Pine Hollow Park,Red,2026-06-01 0900,,6,,,3,3
Pat Example,Pine Hollow Park,Red,2026-06-01 0900,,5,-1,900,2,3
Par,Pine Hollow Park,Blue,2026-06-02 0900,,6,,,3,3
Pat Example,Pine Hollow Park,Blue,2026-06-02 0900,,8,2,700,4,4
Par,Otter Creek - Red Course,Red Long,2026-06-03 0900,,6,,,3,3
Pat Example,Otter Creek - Red Course,Red Long,2026-06-03 0900,,9,3,650,5,4
Par,Otter Creek - Blue Course,Red Short,2026-06-04 0900,,6,,,3,3
Pat Example,Otter Creek - Blue Course,Red Short,2026-06-04 0900,,7,1,750,4,3
"""


def test_a_color_in_another_course_name_doesnt_pull_it_in():
    two = scorecards_from(TWO_REDS, date(2026, 6, 5))
    assert match_courses("what's my best score on the red layout at pine hollow?", two) == ["Pine Hollow Park"]
    # A color still picks between courses that share a distinctive word.
    assert match_courses("what's my best at otter creek red?", two) == ["Otter Creek - Red Course"]
    assert sorted(match_courses("how often do I play otter creek?", two)) == [
        "Otter Creek - Blue Course", "Otter Creek - Red Course"]


def test_course_best_across_layouts_is_computed():
    two = scorecards_from(TWO_REDS, date(2026, 6, 5))
    text, _ = stats_section(two, "what's my best score at pine hollow?")
    assert "Best round of these layouts (lowest vs par): 5 (-1) on Red on 2026-06-01, rating 900" in text
    assert "lower is better" in text
