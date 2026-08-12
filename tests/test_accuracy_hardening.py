"""Accuracy-first invariants added after review of real photographed rosters."""

from __future__ import annotations

from pathlib import Path
import itertools
import random

import pytest
from PIL import Image, ImageDraw

from app.engine.dates import ExtractedDate, HijriDate, extract_all_dates
from app.engine.extract_master import (
    _merge_near_duplicate_people,
    _select_roster_columns,
    crop_table_for_ocr,
    detect_table_grid,
)
from app.engine.extract_targets import match_to_master
from app.engine.models import MasterPerson, NameStatus, make_master_key
from app.engine.ocr import (
    OcrToken,
    highlight_score,
    merge_ocr_observations,
    ocr_max_side,
    ocr_timeout_seconds,
    orient_document_image,
    tesseract_environment,
)
from app.engine.ranking import RankPerson, RankStatus, compare_two, rank_people
from app.engine.pipeline import new_session, run_ranking
from app.engine.models import TargetName


def person(name: str, values: list[tuple[int, int, int]]) -> RankPerson:
    return RankPerson(
        id=name,
        original_name=name,
        normalized_name=make_master_key(name),
        dates=[HijriDate(*value) for value in values],
    )


def test_render_ocr_limits_keep_consensus_practical(monkeypatch):
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://example.onrender.com")
    monkeypatch.delenv("OCR_MAX_SIDE", raising=False)
    monkeypatch.delenv("OCR_TIMEOUT_SECONDS", raising=False)
    assert ocr_max_side() == 2400
    assert ocr_timeout_seconds() == 300

    monkeypatch.setenv("OCR_MAX_SIDE", "2700")
    monkeypatch.setenv("OCR_TIMEOUT_SECONDS", "420")
    assert ocr_max_side() == 2700
    assert ocr_timeout_seconds() == 420


def test_render_limits_tesseract_threads(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.delenv("OMP_THREAD_LIMIT", raising=False)
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    environment = tesseract_environment()
    assert environment["OMP_THREAD_LIMIT"] == "1"
    assert environment["OMP_NUM_THREADS"] == "1"


def test_partial_order_keeps_strict_information_inside_unresolved_component():
    a = person("A", [(1447, 8, 10), (1447, 7, 20), (1447, 6, 4)])
    b = person("B", [(1447, 8, 10), (1447, 7, 20), (1447, 6, 5)])
    c = person("C", [(1447, 8, 10), (1447, 7, 20)])
    results = {entry.person.original_name: entry for entry in rank_people([c, b, a])}

    assert compare_two(a, b)["result"] == "a"
    assert results["A"].status == RankStatus.UNRESOLVED
    assert "B" in results["A"].strictly_before
    assert "A" in results["B"].strictly_after
    assert "C" in results["A"].unresolved_with
    assert results["A"].rank_min < results["B"].rank_max
    assert not results["A"].rank_exact


def test_partial_order_is_permutation_invariant_with_incomparability():
    data = [
        person("A", [(1447, 8, 10), (1447, 7, 20), (1447, 6, 4)]),
        person("B", [(1447, 8, 10), (1447, 7, 20), (1447, 6, 5)]),
        person("C", [(1447, 8, 10), (1447, 7, 20)]),
        person("D", [(1447, 8, 12)]),
    ]
    expected = {
        entry.person.original_name: (
            entry.rank_min,
            entry.rank_max,
            tuple(entry.strictly_before),
            tuple(entry.strictly_after),
            tuple(entry.unresolved_with),
        )
        for entry in rank_people(data)
    }
    actual = {
        entry.person.original_name: (
            entry.rank_min,
            entry.rank_max,
            tuple(entry.strictly_before),
            tuple(entry.strictly_after),
            tuple(entry.unresolved_with),
        )
        for entry in rank_people(list(reversed(data)))
    }
    assert actual == expected


def test_ranking_does_not_mutate_caller_date_sequence():
    record = person("A", [(1447, 1, 1), (1447, 8, 1), (1447, 8, 1)])
    original = list(record.dates)
    result = rank_people([record])
    assert record.dates == original
    assert result[0].person.dates == [HijriDate(1447, 8, 1), HijriDate(1447, 1, 1)]


def test_randomized_strict_relation_is_transitive_and_reproducible():
    rng = random.Random(20260812)
    pool = [
        HijriDate(year, month, day)
        for year in (1446, 1447)
        for month in range(1, 13)
        for day in (1, 8, 15, 22)
    ]
    for case in range(350):
        people = []
        for index in range(8):
            length = rng.randint(1, 9)
            dates = rng.sample(pool, length)
            people.append(
                RankPerson(
                    id=f"{case}-{index}",
                    original_name=f"P{index}",
                    normalized_name=f"p{index}",
                    dates=dates,
                )
            )
        result = {
            entry.person.id: (
                entry.rank_min,
                entry.rank_max,
                tuple(entry.strictly_before),
                tuple(entry.strictly_after),
            )
            for entry in rank_people(people)
        }
        shuffled = list(people)
        rng.shuffle(shuffled)
        result_shuffled = {
            entry.person.id: (
                entry.rank_min,
                entry.rank_max,
                tuple(entry.strictly_before),
                tuple(entry.strictly_after),
            )
            for entry in rank_people(shuffled)
        }
        assert result == result_shuffled
        for a, b, c in itertools.permutations(people, 3):
            ab = compare_two(a, b)["result"]
            bc = compare_two(b, c)["result"]
            if ab == "a" and bc == "a":
                assert compare_two(a, c)["result"] == "a"


def test_similar_master_identities_are_not_automatically_merged():
    names = ["محمد سعد الحارثي", "محمد سعيد الحارثي"]
    master = {
        make_master_key(name): MasterPerson(
            original_name=name, normalized_name=make_master_key(name)
        )
        for name in names
    }
    result = _merge_near_duplicate_people(master)
    assert len(result) == 2
    assert all(person.identity_needs_review for person in result.values())


def test_fuzzy_identity_is_a_proposal_not_automatic_verification():
    name = "محمد سلامة الحازمي"
    master = {
        make_master_key(name): MasterPerson(
            original_name=name, normalized_name=make_master_key(name)
        )
    }
    status, confidence, candidates, matched = match_to_master(
        "محمد سلامه الحازمي", master
    )
    assert status == NameStatus.NEEDS_REVIEW
    assert matched == name
    assert candidates


def test_single_pass_extracted_date_is_not_silently_verified():
    dates = extract_all_dates(
        "اعتبارًا من ١٤٤٧/٠٨/١٥هـ",
        confidence=0.94,
        ocr_agreement=1,
        row_association_confidence=0.98,
    )
    assert len(dates) == 1
    assert not dates[0].verified


def test_two_pass_date_with_strong_row_association_can_verify():
    dates = extract_all_dates(
        "اعتبارًا من ١٤٤٧/٠٨/١٥هـ",
        confidence=0.98,
        ocr_agreement=2,
        row_association_confidence=0.98,
    )
    assert len(dates) == 1
    assert dates[0].verified


def test_duplicate_dates_retain_all_source_occurrences():
    person_record = MasterPerson(
        original_name="أحمد علي", normalized_name=make_master_key("أحمد علي")
    )
    person_record.add_occurrence(
        page=1,
        notes="١٤٤٧/٠٨/١٥",
        date_confidence=0.98,
        ocr_agreement=2,
        row_association_confidence=0.98,
    )
    person_record.add_occurrence(
        page=3,
        notes="١٤٤٧/٠٨/١٥",
        date_confidence=0.98,
        ocr_agreement=2,
        row_association_confidence=0.98,
    )
    assert len(person_record.dates) == 2
    assert {date.page for date in person_record.dates} == {1, 3}


def test_material_pending_date_blocks_affected_person_from_ranking():
    name = "أحمد علي"
    key = make_master_key(name)
    master = MasterPerson(original_name=name, normalized_name=key)
    master.dates.extend(
        [
            ExtractedDate(
                normalized=HijriDate(1447, 8, 1),
                original_text="1447/8/1",
                page=1,
                confidence=0.98,
                verified=True,
            ),
            ExtractedDate(
                normalized=HijriDate(1447, 9, 1),
                original_text="1447/9/?",
                page=2,
                confidence=0.55,
                verified=False,
                needs_review=True,
            ),
        ]
    )
    session = new_session()
    session.master_people = {key: master}
    session.target_names = [
        TargetName(
            id="t1",
            original_name=name,
            normalized_name=key,
            ocr_raw=name,
            confidence=1.0,
            status=NameStatus.VERIFIED,
            matched_master_name=name,
        )
    ]
    run_ranking(session, auto_verify_dates=False)
    assert session.ranking_results[0]["rank"] is None
    assert session.ranking_results[0]["status"] == RankStatus.NEEDS_REVIEW.value
    assert session.summary["blocked_by_pending_dates"] == 1


def test_unverified_duplicate_source_does_not_block_verified_same_date():
    name = "أحمد علي"
    key = make_master_key(name)
    master = MasterPerson(original_name=name, normalized_name=key)
    for verified, page in ((True, 1), (False, 2)):
        master.dates.append(
            ExtractedDate(
                normalized=HijriDate(1447, 8, 1),
                original_text="1447/8/1",
                page=page,
                confidence=0.98 if verified else 0.7,
                verified=verified,
                needs_review=not verified,
            )
        )
    session = new_session()
    session.master_people = {key: master}
    session.target_names = [
        TargetName(
            id="t1",
            original_name=name,
            normalized_name=key,
            ocr_raw=name,
            confidence=1.0,
            status=NameStatus.VERIFIED,
            matched_master_name=name,
        )
    ]
    run_ranking(session, auto_verify_dates=False)
    assert session.ranking_results[0]["rank"] == 1


def test_ocr_consensus_records_alternative_disagreement():
    tokens = [
        OcrToken("١٤٤٧/٨/١٥", 0.1, 0.5, 0.2, 0.03, 0.98, source="a"),
        OcrToken("١٤٤٧/٨/١٥", 0.1, 0.5, 0.2, 0.03, 0.96, source="b"),
        OcrToken("١٤٤٧/٨/١٦", 0.1, 0.5, 0.2, 0.03, 0.91, source="c"),
    ]
    merged = merge_ocr_observations(tokens)
    assert len(merged) == 1
    assert merged[0].agreement == 2
    assert merged[0].text == "١٤٤٧/٨/١٥"
    assert any(alt["text"] == "١٤٤٧/٨/١٦" for alt in merged[0].alternatives)


def test_highlight_detection_selects_yellow_region(tmp_path: Path):
    path = tmp_path / "highlight.png"
    image = Image.new("RGB", (600, 300), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((300, 90, 550, 145), fill=(225, 255, 0))
    image.save(path)
    highlighted = OcrToken("اسم مظلل", 0.50, 1 - (145 / 300), 0.40, 55 / 300)
    plain = OcrToken("اسم عادي", 0.10, 1 - (250 / 300), 0.30, 40 / 300)
    assert highlight_score(path, highlighted) > 0.20
    assert highlight_score(path, plain) < 0.02


def test_document_orientation_applies_clockwise_osd_correction(
    tmp_path: Path, monkeypatch
):
    source = tmp_path / "sideways.png"
    image = Image.new("RGB", (120, 60), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 59, 59), fill="red")
    draw.rectangle((60, 0, 119, 59), fill="blue")
    image.save(source)

    monkeypatch.setattr("app.engine.ocr._tesseract_osd_rotation", lambda _: 90)
    oriented = orient_document_image(source)
    try:
        with Image.open(oriented) as result:
            assert result.size == (60, 120)
            assert result.getpixel((30, 20))[0] > 200
            assert result.getpixel((30, 100))[2] > 200
    finally:
        oriented.unlink(missing_ok=True)


def test_table_grid_detection_on_synthetic_roster(tmp_path: Path):
    sample = tmp_path / "roster.png"
    image = Image.new("L", (1000, 1400), 255)
    draw = ImageDraw.Draw(image)
    verticals = [20, 690, 745, 900, 970]
    horizontals = list(range(80, 1321, 55))
    for x in verticals:
        draw.line((x, 80, x, 1320), fill=0, width=4)
    for y in horizontals:
        draw.line((20, y, 970, y), fill=0, width=4)
    image.save(sample)
    grid = detect_table_grid(sample)
    assert grid is not None
    assert len(grid.vertical_lines) >= 5
    assert len(grid.row_boundaries) >= 5


def test_table_crop_remaps_geometry_and_removes_page_furniture(tmp_path: Path):
    sample = tmp_path / "page.png"
    Image.new("RGB", (1000, 1400), "white").save(sample)
    from app.engine.extract_master import TableGrid

    grid = TableGrid(
        row_boundaries=(0.20, 0.30, 0.80),
        vertical_lines=(0.10, 0.40, 0.70, 0.90),
        name_left=0.70,
        name_right=0.90,
        notes_right=0.40,
    )
    cropped_path, cropped_grid = crop_table_for_ocr(sample, grid, padding=0.0)
    try:
        with Image.open(cropped_path) as cropped:
            assert cropped.size == (800, 840)
        assert cropped_grid.row_boundaries == (0.0, pytest.approx(1 / 6), 1.0)
        assert cropped_grid.vertical_lines == (0.0, pytest.approx(0.375), pytest.approx(0.75), 1.0)
        assert cropped_grid.name_left == pytest.approx(0.75)
        assert cropped_grid.notes_right == pytest.approx(0.375)
    finally:
        cropped_path.unlink(missing_ok=True)


def test_roster_column_selection_rejects_aligned_name_ink_as_rule():
    # 0.857 is a false projection caused by repeated right-aligned name text.
    # The actual name cell spans 0.770..0.926.
    selected = _select_roster_columns(
        [0.044, 0.717, 0.770, 0.857, 0.926, 0.985]
    )
    assert selected is not None
    notes_right, name_left, name_right = selected
    assert notes_right == 0.717
    assert name_left == 0.770
    assert name_right == 0.926
