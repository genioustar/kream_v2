"""사이트별 상품명 표기를 공통 모델명 목록으로 변환한다."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable


MODEL_STRATEGIES: frozenset[str] = frozenset(
    {
        "auto",
        "bracket_first",
        "slash_first",
        "suffix_grouped",
        "suffix_single",
    }
)

_MAX_MODEL_LENGTH = 80
_MAX_GROUP_TOKENS = 5
_BRACKET_RE = re.compile(r"\(([^()]*)\)")
_ASCII_RUN_RE = re.compile(
    r"(?<![A-Za-z0-9-])"
    r"(?:[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*"
    r"(?:\s+[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)*)"
    r"(?![A-Za-z0-9-])"
)
_PLUS_VARIANT_RE = re.compile(
    r"(?P<code>(?=[A-Za-z0-9-]*[A-Za-z])"
    r"(?=[A-Za-z0-9-]*\d)[A-Za-z0-9-]{4,})\+\d{3,}\b"
)
_SPACED_SEASON_RE = re.compile(
    r"\b(?:(?:\d{2}|\d{4})\s+(?:SS|FW)|(?:SS|FW)\s+(?:\d{2}|\d{4}))\b",
    re.IGNORECASE,
)
_SIZE_RANGE_RE = re.compile(
    r"^(?:"
    r"SIZE\d{2,3}(?:[-~]\d{2,3})?(?:MM|CM)?|"
    r"\d{2,3}[-~]\d{2,3}(?:MM|CM)?|"
    r"\d{2,3}(?:MM|CM)"
    r")$"
)
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
_YEAR_RANGE_RE = re.compile(r"^(?:19|20)\d{2}[-~](?:\d{2}|(?:19|20)\d{2})$")
_SEASON_RE = re.compile(
    r"^(?:(?:\d{2}|\d{4})(?:SS|FW)|(?:SS|FW)(?:\d{2}|\d{4})|00S)$"
)
_COUNT_RE = re.compile(r"^(\d{1,3})(P|PACK|PCS?|COLORS?|WAYS?)$")
_UNIT_RE = re.compile(r"^\d{1,3}(?:CM|MM|ML|CC|KG|G|L|IN)$")
_WIDTH_RE = re.compile(r"^\d(?:E|EE)$")
_NON_MODEL_TOKEN_RE = re.compile(
    r"^(?:"
    r"D|GS|PS|TD|M|W|XS|S|L|XL|XXL|SS|FW|"
    r"BLACK|WHITE|RED|BLUE|GREEN|GRAY|GREY|NAVY|BEIGE|BROWN|NATURAL|"
    r"IVORY|SILVER|GOLD|PURPLE|ORANGE|YELLOW|KHAKI|CREAM|PINK|"
    r"MINT|CHARCOAL|BURGUNDY|CAMEL"
    r")$"
)


def _prepare_text(value: str) -> str:
    """유니코드/공백을 정규화하고 목록용 ``CODE+번호``의 보조 번호를 제거한다."""
    normalized = unicodedata.normalize("NFKC", value)
    normalized = _SPACED_SEASON_RE.sub(" ", normalized)
    normalized = _PLUS_VARIANT_RE.sub(r"\g<code>", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _is_noise_token(token: str) -> bool:
    upper = token.upper()
    if _SIZE_RANGE_RE.fullmatch(upper):
        return True
    if _YEAR_RE.fullmatch(upper):
        return True
    if _YEAR_RANGE_RE.fullmatch(upper):
        return True
    if _SEASON_RE.fullmatch(upper):
        return True
    if _WIDTH_RE.fullmatch(upper):
        return True
    if _NON_MODEL_TOKEN_RE.fullmatch(upper):
        return True

    count_match = _COUNT_RE.fullmatch(upper)
    if count_match:
        number = int(count_match.group(1))
        unit = count_match.group(2)
        return unit != "P" or number <= 20

    unit_match = _UNIT_RE.fullmatch(upper)
    if unit_match:
        return True

    return False


def _token_kind(token: str, *, allow_letter_hyphen: bool) -> str:
    """ASCII 토큰을 strong/weak/noise/invalid 중 하나로 분류한다."""
    if not token or _is_noise_token(token):
        return "noise"

    compact = token.replace("-", "")
    if not compact.isalnum():
        return "invalid"

    has_alpha = any(char.isalpha() for char in compact)
    has_digit = any(char.isdigit() for char in compact)

    if has_alpha and has_digit and len(compact) >= 4:
        return "strong"
    if has_digit and "-" in token and len(compact) >= 5:
        return "strong"
    if token.isdigit() and len(token) >= 5:
        return "strong"
    if (
        allow_letter_hyphen
        and has_alpha
        and not has_digit
        and "-" in token
        and len(compact) >= 6
    ):
        return "strong"

    if has_digit:
        return "weak"
    if token.isupper() and len(compact) >= 2:
        return "weak"
    return "invalid"


def _candidate_chunks(
    run: str,
    *,
    grouped: bool,
    allow_letter_hyphen: bool,
    include_all_weak_edges: bool,
) -> list[str]:
    """
    한 ASCII run에서 모델 후보를 만든다.

    ``suffix_grouped`` 사이트는 마지막 ASCII run 전체가 판매처 SKU인 경우가
    많아 영문-only 세그먼트도 함께 보존한다. 일반 사이트에서는 strong 토큰
    주변의 숫자/대문자 세그먼트만 포함해 색상·설명어 오탐을 줄인다.
    """
    tokens = run.split()
    classified = [
        (token, _token_kind(token, allow_letter_hyphen=allow_letter_hyphen))
        for token in tokens
    ]

    chunks: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    for item in classified:
        if item[1] in {"noise", "invalid"}:
            if current:
                chunks.append(current)
                current = []
            continue
        current.append(item)
    if current:
        chunks.append(current)

    candidates: list[str] = []
    for chunk in chunks:
        strong_indexes = [
            index for index, (_, kind) in enumerate(chunk) if kind == "strong"
        ]
        if not strong_indexes:
            continue

        if not grouped:
            candidates.extend(
                token for token, kind in chunk if kind == "strong"
            )
            continue

        # 긴 숫자 여러 개만 연속되면 하나의 SKU가 아니라 대체 모델인 경우가 많다.
        if len(chunk) > 1 and all(
            kind == "strong" and token.isdigit() for token, kind in chunk
        ):
            candidates.extend(token for token, _ in chunk)
            continue

        start = 0 if include_all_weak_edges else strong_indexes[0]
        end = len(chunk) - 1 if include_all_weak_edges else strong_indexes[-1]

        if not include_all_weak_edges:
            while end + 1 < len(chunk):
                following = chunk[end + 1][0]
                if any(char.isdigit() for char in following):
                    end += 1
                else:
                    break

        selected = [token for token, _ in chunk[start : end + 1]]
        if len(selected) > _MAX_GROUP_TOKENS:
            selected = selected[-_MAX_GROUP_TOKENS:]
        candidates.append(" ".join(selected))

    return candidates


def _extract_from_runs(
    text: str,
    *,
    grouped: bool,
    allow_letter_hyphen: bool,
    include_all_weak_edges: bool = False,
) -> list[str]:
    candidates: list[str] = []
    for match in _ASCII_RUN_RE.finditer(text):
        candidates.extend(
            _candidate_chunks(
                match.group(),
                grouped=grouped,
                allow_letter_hyphen=allow_letter_hyphen,
                include_all_weak_edges=include_all_weak_edges,
            )
        )
    return candidates


def _extract_bracket_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    for content in _BRACKET_RE.findall(text):
        parts = [part.strip() for part in content.split("/") if part.strip()]
        parsed_parts = [
            _extract_from_runs(
                part,
                grouped=True,
                allow_letter_hyphen=True,
                include_all_weak_edges=True,
            )
            for part in parts
        ]

        valid_parts = [part for part in parsed_parts if part]
        if len(parts) > 1 and len(valid_parts) >= 2:
            for part in valid_parts:
                candidates.extend(part)
            continue

        candidates.extend(
            _extract_from_runs(
                content,
                grouped=True,
                allow_letter_hyphen=True,
                include_all_weak_edges=True,
            )
        )
    return candidates


def _extract_bracket_slash_candidates(text: str) -> list[str]:
    """괄호 안에서 ``/``로 명시된 복수 모델 후보만 추출한다."""
    candidates: list[str] = []
    for content in _BRACKET_RE.findall(text):
        if "/" in content:
            candidates.extend(_extract_bracket_candidates(f"({content})"))
    return candidates


def _extract_slash_candidates(
    text: str,
    *,
    grouped: bool,
    rightmost_only: bool = False,
) -> list[str]:
    if "/" not in text:
        return []

    parsed_segments = [
        _extract_from_runs(
            segment,
            grouped=grouped,
            allow_letter_hyphen=False,
        )
        for segment in text.split("/")
    ]
    if rightmost_only:
        return next(
            (segment for segment in reversed(parsed_segments) if segment),
            [],
        )

    valid_segments = [segment for segment in parsed_segments if segment]
    if len(valid_segments) >= 2:
        return [candidate for segment in valid_segments for candidate in segment]
    return valid_segments[0] if valid_segments else []


def _extract_suffix_candidates(
    text: str,
    *,
    grouped: bool,
    include_all_weak_edges: bool,
) -> list[str]:
    matches = list(_ASCII_RUN_RE.finditer(text))
    for match in reversed(matches):
        candidates = _candidate_chunks(
            match.group(),
            grouped=grouped,
            allow_letter_hyphen=include_all_weak_edges,
            include_all_weak_edges=include_all_weak_edges,
        )
        if candidates:
            return candidates
    return []


def _deduplicate(candidates: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for candidate in candidates:
        normalized = re.sub(r"\s+", " ", candidate).strip(" -")
        if not normalized:
            continue
        normalized = normalized[:_MAX_MODEL_LENGTH]
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(normalized)
    return unique


def extract_model_names(product_name: str, strategy: str = "auto") -> list[str]:
    """
    상품명에서 사이트 표기에 맞는 Kream 검색용 모델명 목록을 추출한다.

    전략:
    - ``bracket_first``: 괄호 코드를 우선하는 스토어
    - ``slash_first``: 마지막 ``/`` 뒤 스타일코드를 우선하는 무신사형 표기
    - ``suffix_single``: 제목 끝 단일 코드가 주류인 스토어
    - ``suffix_grouped``: ``A21873 0BKBW 900`` 같은 복합 SKU 스토어
    - ``auto``: 괄호 → 슬래시 → 후행 복합 → 본문 후보 순으로 자동 탐색
    """
    if strategy not in MODEL_STRATEGIES:
        allowed = ", ".join(sorted(MODEL_STRATEGIES))
        raise ValueError(f"알 수 없는 모델명 추출 전략: {strategy!r}. 허용값: {allowed}")
    if not product_name or not product_name.strip():
        return []

    text = _prepare_text(product_name)
    bracket: Callable[[], list[str]] = lambda: _extract_bracket_candidates(text)
    bracket_slash: Callable[[], list[str]] = (
        lambda: _extract_bracket_slash_candidates(text)
    )
    slash_grouped: Callable[[], list[str]] = lambda: _extract_slash_candidates(
        text, grouped=True
    )
    slash_rightmost: Callable[[], list[str]] = lambda: _extract_slash_candidates(
        text,
        grouped=True,
        rightmost_only=True,
    )
    suffix_grouped: Callable[[], list[str]] = lambda: _extract_suffix_candidates(
        text,
        grouped=True,
        include_all_weak_edges=strategy == "suffix_grouped",
    )
    suffix_single: Callable[[], list[str]] = lambda: _extract_suffix_candidates(
        text,
        grouped=False,
        include_all_weak_edges=False,
    )
    inline_grouped: Callable[[], list[str]] = lambda: _extract_from_runs(
        text,
        grouped=True,
        allow_letter_hyphen=strategy == "suffix_grouped",
        include_all_weak_edges=strategy == "suffix_grouped",
    )
    inline_single: Callable[[], list[str]] = lambda: _extract_from_runs(
        text,
        grouped=False,
        allow_letter_hyphen=False,
    )

    if strategy == "slash_first":
        stages = (
            bracket_slash,
            slash_rightmost,
            bracket,
            suffix_single,
            inline_grouped,
        )
    elif strategy == "suffix_single":
        stages = (bracket, slash_grouped, suffix_single, inline_single)
    elif strategy == "suffix_grouped":
        stages = (bracket, slash_grouped, suffix_grouped, inline_grouped)
    else:
        stages = (bracket, slash_grouped, suffix_grouped, inline_grouped)

    for stage in stages:
        candidates = _deduplicate(stage())
        if candidates:
            return candidates
    return []
