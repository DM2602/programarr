"""Fixed values for the filter forms: TMDB genres, country and language codes."""
from __future__ import annotations

# One entry per genre across movies and shows. TMDB merges some genres for shows
# (Action & Adventure, Sci-Fi & Fantasy, War & Politics), so those match both halves.
# key: (label, movie genre ids, TV genre ids)
GENRES: dict[str, tuple[str, tuple[int, ...], tuple[int, ...]]] = {
    "action": ("Action", (28,), (10759,)),
    "adventure": ("Adventure", (12,), (10759,)),
    "animation": ("Animation", (16,), (16,)),
    "comedy": ("Comedy", (35,), (35,)),
    "crime": ("Crime", (80,), (80,)),
    "documentary": ("Documentary", (99,), (99,)),
    "drama": ("Drama", (18,), (18,)),
    "family": ("Family", (10751,), (10751,)),
    "fantasy": ("Fantasy", (14,), (10765,)),
    "history": ("History", (36,), ()),
    "horror": ("Horror", (27,), ()),
    "kids": ("Kids", (), (10762,)),
    "music": ("Music", (10402,), ()),
    "mystery": ("Mystery", (9648,), (9648,)),
    "news": ("News", (), (10763,)),
    "reality": ("Reality", (), (10764,)),
    "romance": ("Romance", (10749,), ()),
    "science-fiction": ("Science Fiction", (878,), (10765,)),
    "soap": ("Soap", (), (10766,)),
    "talk": ("Talk", (), (10767,)),
    "thriller": ("Thriller", (53,), ()),
    "tv-movie": ("TV Movie", (10770,), ()),
    "war": ("War & Politics", (10752,), (10768,)),
    "western": ("Western", (37,), (37,)),
}

GENRE_NAMES: dict[int, str] = {}
for _label, _movie, _tv in GENRES.values():
    for _id in (*_movie, *_tv):
        GENRE_NAMES.setdefault(_id, _label)
GENRE_NAMES.update({10759: "Action & Adventure", 10765: "Sci-Fi & Fantasy", 10768: "War & Politics"})


def genre_ids(key: str, kind: str) -> tuple[int, ...]:
    _, movie, tv = GENRES[key]
    return movie if kind == "movie" else tv


COUNTRIES = (
    "AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ "
    "CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR "
    "GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO "
    "JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR "
    "MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO "
    "RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV "
    "TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW"
).split()

# ISO 639-1 plus TMDB's own codes: cn (Cantonese) and xx (no language).
LANGUAGES = (
    "aa ab ae af ak am an ar as av ay az ba be bg bi bm bn bo br bs ca ce ch cn co cr cs cu cv cy da de dv dz ee el en "
    "eo es et eu fa ff fi fj fo fr fy ga gd gl gn gu gv ha he hi ho hr ht hu hy hz ia id ie ig ii ik io is it iu ja jv "
    "ka kg ki kj kk kl km kn ko kr ks ku kv kw ky la lb lg li ln lo lt lu lv mg mh mi mk ml mn mr ms mt my na nb nd ne "
    "ng nl nn no nr nv ny oc oj om or os pa pi pl ps pt qu rm rn ro ru rw sa sc sd se sg si sk sl sm sn so sq sr ss st "
    "su sv sw ta te tg th ti tk tl tn to tr ts tt tw ty ug uk ur uz ve vi vo wa wo xh xx yi yo za zh zu"
).split()
