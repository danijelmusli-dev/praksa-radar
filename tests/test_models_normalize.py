from datetime import date

import pytest

from radar.models import RawItem, canonical_url
from radar.normalize import detect_location, extract_deadline, guess_type, to_opportunity

TODAY = date(2026, 11, 6)


@pytest.mark.parametrize(
    "url,expected",
    [
        (
            "HTTPS://Example.com/a/b/?utm_source=x&id=3&fbclid=zz#top",
            "https://example.com/a/b?id=3",
        ),
        ("https://example.com/p?b=2&a=1", "https://example.com/p?a=1&b=2"),
        ("https://example.com/", "https://example.com"),
        (
            "https://www.linkedin.com/comm/jobs/view/3901234567?trackingId=abc&refId=x",
            "https://www.linkedin.com/jobs/view/3901234567",
        ),
        (
            "https://rs.linkedin.com/jobs/view/ml-intern-at-firma-3901234567/?trk=eml",
            "https://www.linkedin.com/jobs/view/3901234567",
        ),
        (
            "https://www.linkedin.com/jobs/collections/recommended/?currentJobId=3901234567",
            "https://www.linkedin.com/jobs/view/3901234567",
        ),
        (
            "https://mail.google.com/mail/u/0/#all/18c0ffee",
            "https://mail.google.com/mail/u/0#all/18c0ffee",
        ),
    ],
)
def test_canonical_url(url, expected):
    assert canonical_url(url) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Rok za prijavu: 20.11.2026.", date(2026, 11, 20)),
        ("rok 20.11.", date(2026, 11, 20)),
        ("rok 20.10.", date(2027, 10, 20)),  # već prošlo ove godine -> sledeća
        ("Prijave do 5. decembra 2026", date(2026, 12, 5)),
        ("prijave do 12. septembra", date(2027, 9, 12)),
        ("Deadline: 15 November 2026", date(2026, 11, 15)),
        ("Deadline: December 5, 2026", date(2026, 12, 5)),
        ("apply by 01/12/2026", date(2026, 12, 1)),
        ("Rok: 1-12-2026", date(2026, 12, 1)),
        ("Rok: 21.11.2026.", date(2026, 11, 21)),
        ("traži se do 2.5 godine iskustva", None),
        ("do 5-6 godina iskustva", None),
        ("do 5 junior inženjera", None),
        ("Događaj 20.11.2026", None),  # bez ključne reči
        ("Rok: 31.02.2026", None),  # nepostojeći datum
        ("", None),
    ],
)
def test_extract_deadline(text, expected):
    assert extract_deadline(text, TODAY) == expected


def test_extract_deadline_takes_first_valid():
    assert extract_deadline("Rok: 31.02.2026 ili rok 10.12.2026", TODAY) == date(2026, 12, 10)


@pytest.mark.parametrize(
    "meta,title,text,expected",
    [
        ("Beograd | Hibrid", "x", "", "Beograd | Hibrid"),  # meta ima prednost
        (None, "ML Intern", "Radno mesto: Novi Sad, Srbija", "Novi Sad"),
        (None, "Intern", "Radiš sa Niša", None),
        (None, "Intern (Remote)", "", "remote"),
        (None, "Intern", "Radno vreme fleksibilno", None),
    ],
)
def test_detect_location(meta, title, text, expected):
    assert detect_location(meta, title, text) == expected


@pytest.mark.parametrize(
    "title,text,expected",
    [
        ("ML Intern", "", "praksa"),
        ("Letnja praksa 2027", "", "praksa"),
        ("Embedded Weekend", "Radionica za studente", "radionica"),
        ("Hackathon 2026", "", "takmičenje"),
        ("Junior Python Developer", "", "posao"),
        ("International Sales Lead", "", "posao"),  # 'intern' unutar reči ne računa
        ("Studentski projekat", "", "projekat"),
        ("Akademija za Java developere", "", "praksa"),
    ],
)
def test_guess_type(title, text, expected):
    assert guess_type(title, text) == expected


def test_to_opportunity():
    raw = RawItem(
        source="helloworld",
        url="https://www.helloworld.rs/posao/ML-Intern/Firma/1?utm_source=x",
        title="  ML   Intern ",
        text="ML Intern Firma Beograd Rok: 21.11.2026.",
        meta={"org": "Firma", "location": None},
    )
    o = to_opportunity(raw, TODAY)
    assert o.url == "https://www.helloworld.rs/posao/ML-Intern/Firma/1"
    assert o.title == "ML Intern"
    assert o.org == "Firma"
    assert o.location == "Beograd"
    assert o.deadline == date(2026, 11, 21)
    assert o.type == "praksa"
    assert o.source == "helloworld"
