"""Whole-pipeline deterministic event, feature, score, and risk fixtures."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from pydantic import TypeAdapter

from ml.features.config import MLConfig
from ml.features.extractor import extract_windows
from ml.features.schema import (
    ContextEvent,
    FeatureWindow,
    Heartbeat,
    KeyboardEvent,
    MouseButton,
    MouseEvent,
    Provenance,
    QualityLabel,
)
from protocol.generated.python.contracts import (
    PROTOCOL_VERSION,
    DataProvenance,
    EventFrame,
    KeyClass,
    ModalityScore,
    ModelStatus,
    RiskLevel,
    ScoreResult,
)
from protocol.generated.python.contracts import FeatureWindow as GeneratedFeatureWindow
from tools.synthetic.config import (
    BehavioralProfile,
    ScenarioConfig,
    ScorePhase,
    SyntheticConfig,
)
from tools.synthetic.framing import FramedStreams, SyntheticEvent, build_framed_streams

EVENT_ADAPTER: TypeAdapter[EventFrame] = TypeAdapter(EventFrame)
FEATURE_ADAPTER: TypeAdapter[GeneratedFeatureWindow] = TypeAdapter(GeneratedFeatureWindow)
SCORE_ADAPTER: TypeAdapter[ScoreResult] = TypeAdapter(ScoreResult)


class SyntheticGenerationError(ValueError):
    """Raised instead of silently clipping or emitting an invalid fixture."""


@dataclass(frozen=True)
class WindowExpectation:
    window_id: str
    quality_label: QualityLabel
    phase: str
    expected_risk_level: RiskLevel


@dataclass(frozen=True)
class ScenarioExpectations:
    provenance: DataProvenance
    headline_evaluation_allowed: bool
    event_intervals_us: tuple[int, ...]
    takeover_at_us: int | None
    windows: tuple[WindowExpectation, ...]


@dataclass(frozen=True)
class ScenarioBundle:
    name: str
    events: tuple[SyntheticEvent, ...]
    feature_windows: tuple[FeatureWindow, ...]
    scores: tuple[ScoreResult, ...]
    expectations: ScenarioExpectations
    framed_streams: FramedStreams


def _positive_sample(
    rng: random.Random,
    *,
    mean: float,
    std: float,
    floor: float,
    attempts: int,
) -> float:
    for _ in range(attempts):
        value = rng.gauss(mean, std)
        if value >= floor:
            return value
    raise SyntheticGenerationError(
        "configured distribution could not produce a value above the configured minimum; "
        "generation stopped without clipping"
    )


def _choose_key_class(rng: random.Random, profile: BehavioralProfile) -> KeyClass:
    classes = tuple(profile.key_class_weights)
    weights = tuple(profile.key_class_weights[item] for item in classes)
    return rng.choices(classes, weights=weights, k=1)[0]


def _keyboard_events(
    rng: random.Random,
    profile: BehavioralProfile,
    scenario: ScenarioConfig,
    *,
    start_us: int,
    end_us: int,
    sample_floor: float,
    sample_attempts: int,
) -> list[SyntheticEvent]:
    if not scenario.keyboard_enabled:
        return []
    events: list[SyntheticEvent] = []
    current = float(start_us)
    while current < end_us:
        dwell = _positive_sample(
            rng,
            mean=profile.mean_dwell_us,
            std=profile.std_dwell_us,
            floor=sample_floor,
            attempts=sample_attempts,
        )
        release = current + dwell
        if release >= end_us:
            break
        key_class = _choose_key_class(rng, profile)
        events.extend(
            (
                KeyboardEvent(
                    type="KEY_DOWN",
                    t_capture_us=int(current),
                    key_class=key_class,
                    is_repeat=False,
                    device_class=profile.keyboard_device,
                    app_id=scenario.app_id,
                    seq=0,
                ),
                KeyboardEvent(
                    type="KEY_UP",
                    t_capture_us=int(release),
                    key_class=key_class,
                    is_repeat=False,
                    device_class=profile.keyboard_device,
                    app_id=scenario.app_id,
                    seq=0,
                ),
            )
        )
        current += _positive_sample(
            rng,
            mean=profile.mean_key_interval_us,
            std=profile.std_key_interval_us,
            floor=sample_floor,
            attempts=sample_attempts,
        )
    return events


def _mouse_events(
    rng: random.Random,
    profile: BehavioralProfile,
    scenario: ScenarioConfig,
    *,
    start_us: int,
    end_us: int,
    sample_floor: float,
    distance_floor: float,
    sample_attempts: int,
) -> list[SyntheticEvent]:
    if not scenario.mouse_enabled:
        return []
    events: list[SyntheticEvent] = []
    current = start_us
    x = float(scenario.initial_mouse_x)
    y = float(scenario.initial_mouse_y)
    while current + profile.mouse_step_us < end_us:
        current += profile.mouse_step_us
        distance = _positive_sample(
            rng,
            mean=profile.mouse_distance_mean_px,
            std=profile.mouse_distance_std_px,
            floor=distance_floor,
            attempts=sample_attempts,
        )
        angle = rng.uniform(0, math.tau)
        x += math.cos(angle) * distance
        y += math.sin(angle) * distance
        x_int, y_int = int(round(x)), int(round(y))
        event_draw = rng.random()
        if event_draw < profile.mouse_click_probability:
            events.extend(
                (
                    MouseEvent(
                        type="BUTTON_DOWN",
                        t_capture_us=current,
                        x=x_int,
                        y=y_int,
                        button=MouseButton.LEFT,
                        device_class=profile.mouse_device,
                        app_id=scenario.app_id,
                        seq=0,
                    ),
                    MouseEvent(
                        type="BUTTON_UP",
                        t_capture_us=current + int(sample_floor),
                        x=x_int,
                        y=y_int,
                        button=MouseButton.LEFT,
                        device_class=profile.mouse_device,
                        app_id=scenario.app_id,
                        seq=0,
                    ),
                )
            )
        elif event_draw < profile.mouse_click_probability + profile.mouse_scroll_probability:
            events.append(
                MouseEvent(
                    type="SCROLL",
                    t_capture_us=current,
                    x=x_int,
                    y=y_int,
                    scroll_dy=rng.choice((-1, 1)),
                    device_class=profile.mouse_device,
                    app_id=scenario.app_id,
                    seq=0,
                )
            )
        else:
            events.append(
                MouseEvent(
                    type="MOVE",
                    t_capture_us=current,
                    x=x_int,
                    y=y_int,
                    device_class=profile.mouse_device,
                    app_id=scenario.app_id,
                    seq=0,
                )
            )
    return events


def _context_events(
    profile: BehavioralProfile,
    scenario: ScenarioConfig,
    *,
    start_us: int,
    end_us: int,
) -> list[SyntheticEvent]:
    if not scenario.context_enabled:
        return []
    events: list[SyntheticEvent] = []
    current = start_us
    category_index = 0
    while current < end_us:
        category = profile.context_categories[category_index % len(profile.context_categories)]
        events.append(
            ContextEvent(
                t_capture_us=current,
                app_id=scenario.app_id,
                category=category,
                seq=0,
            )
        )
        category_index += 1
        current += scenario.context_switch_interval_us
    return events


def _heartbeat_events(scenario: ScenarioConfig) -> list[SyntheticEvent]:
    if not scenario.heartbeat_enabled:
        return []
    events: list[SyntheticEvent] = []
    current = scenario.t_start_us
    end_us = scenario.t_start_us + scenario.duration_us
    while current < end_us:
        events.append(
            Heartbeat(
                t_capture_us=current,
                collector_uptime_ms=(current - scenario.t_start_us) // 1000,
                dropped_events=0,
                buffer_high_water=0,
                collection_paused=False,
                seq=0,
            )
        )
        current += scenario.heartbeat_interval_us
    return events


def _profile_ranges(
    config: SyntheticConfig, scenario: ScenarioConfig
) -> list[tuple[BehavioralProfile, int, int]]:
    start_us = scenario.t_start_us
    end_us = start_us + scenario.duration_us
    primary = config.profiles[scenario.profile]
    if scenario.takeover_profile is None or scenario.takeover_fraction is None:
        return [(primary, start_us, end_us)]
    takeover_at = start_us + int(scenario.duration_us * scenario.takeover_fraction)
    return [
        (primary, start_us, takeover_at),
        (config.profiles[scenario.takeover_profile], takeover_at, end_us),
    ]


def _assign_sequences(events: list[SyntheticEvent]) -> tuple[SyntheticEvent, ...]:
    ordered = sorted(events, key=lambda event: event.t_capture_us)
    sequenced: list[SyntheticEvent] = []
    for sequence, event in enumerate(ordered):
        document = event.model_dump(mode="python")
        document["seq"] = sequence
        rebuilt = type(event).model_validate(document)
        EVENT_ADAPTER.validate_python(rebuilt.model_dump(mode="python"))
        sequenced.append(rebuilt)
    return tuple(sequenced)


def _phase_for_fraction(scenario: ScenarioConfig, fraction: float) -> ScorePhase:
    for phase in scenario.score_phases:
        if phase.start_fraction <= fraction < phase.end_fraction or (
            fraction == 1 and phase.end_fraction == 1
        ):
            return phase
    raise SyntheticGenerationError(f"no score phase covers fraction {fraction}")


def _scores_and_expectations(
    scenario: ScenarioConfig,
    windows: Sequence[FeatureWindow],
) -> tuple[tuple[ScoreResult, ...], tuple[WindowExpectation, ...]]:
    scores: list[ScoreResult] = []
    expectations: list[WindowExpectation] = []
    for window in windows:
        fraction = (window.t_end_us - scenario.t_start_us) / scenario.duration_us
        phase = _phase_for_fraction(scenario, min(fraction, 1.0))
        keyboard_available = window.keyboard_features is not None
        mouse_available = window.mouse_features is not None
        score = ScoreResult(
            schema_version=PROTOCOL_VERSION,
            user_id=scenario.user_id,
            profile_version="synthetic-profile-v1",
            feature_schema_version=PROTOCOL_VERSION,
            model_version="synthetic-score-v1",
            window_id=window.window_id,
            quality_label=window.quality_label,
            keyboard=ModalityScore(
                available=keyboard_available,
                raw_score=phase.keyboard_risk_score if keyboard_available else None,
                calibrated_score=phase.keyboard_risk_score if keyboard_available else None,
                status=ModelStatus.SCORED if keyboard_available else ModelStatus.UNAVAILABLE,
            ),
            mouse=ModalityScore(
                available=mouse_available,
                raw_score=phase.mouse_risk_score if mouse_available else None,
                calibrated_score=phase.mouse_risk_score if mouse_available else None,
                status=ModelStatus.SCORED if mouse_available else ModelStatus.UNAVAILABLE,
            ),
        )
        SCORE_ADAPTER.validate_python(score.model_dump(mode="python"))
        scores.append(score)
        expectations.append(
            WindowExpectation(
                window_id=window.window_id,
                quality_label=QualityLabel(window.quality_label),
                phase=phase.name,
                expected_risk_level=phase.expected_risk_level,
            )
        )
    return tuple(scores), tuple(expectations)


def generate_scenario(
    config: SyntheticConfig,
    name: str,
    *,
    ml_config: MLConfig,
) -> ScenarioBundle:
    """Generate one validated scenario without consulting clock, OS, or participant data."""

    try:
        scenario = config.scenarios[name]
    except KeyError as exc:
        raise SyntheticGenerationError(f"unknown synthetic scenario {name!r}") from exc

    try:
        date.fromisoformat(scenario.collection_day)
    except ValueError as exc:
        raise SyntheticGenerationError(
            f"collection_day must be an ISO calendar date: {scenario.collection_day!r}"
        ) from exc

    rng = random.Random(scenario.seed)
    unsequenced: list[SyntheticEvent] = []
    for profile, start_us, end_us in _profile_ranges(config, scenario):
        unsequenced.extend(
            _keyboard_events(
                rng,
                profile,
                scenario,
                start_us=start_us,
                end_us=end_us,
                sample_floor=config.sampling.minimum_interval_us,
                sample_attempts=config.sampling.max_attempts,
            )
        )
        unsequenced.extend(
            _mouse_events(
                rng,
                profile,
                scenario,
                start_us=start_us,
                end_us=end_us,
                sample_floor=config.sampling.minimum_interval_us,
                distance_floor=config.sampling.minimum_distance_px,
                sample_attempts=config.sampling.max_attempts,
            )
        )
        unsequenced.extend(_context_events(profile, scenario, start_us=start_us, end_us=end_us))
    unsequenced.extend(_heartbeat_events(scenario))
    events = _assign_sequences(unsequenced)
    if not events:
        raise SyntheticGenerationError("scenario generated no events")

    keyboard = [event for event in events if isinstance(event, KeyboardEvent)]
    mouse = [event for event in events if isinstance(event, MouseEvent)]
    context = [event for event in events if isinstance(event, ContextEvent)]
    windows = extract_windows(
        keyboard,
        mouse,
        context,
        user_id=scenario.user_id,
        session_id=scenario.session_id,
        segment_id=scenario.segment_id,
        collection_day=scenario.collection_day,
        provenance=Provenance.SYNTHETIC,
        config=ml_config,
    )
    for window in windows:
        FEATURE_ADAPTER.validate_python(window.model_dump(mode="python"))

    scores, window_expectations = _scores_and_expectations(scenario, windows)
    takeover_at = (
        scenario.t_start_us + int(scenario.duration_us * scenario.takeover_fraction)
        if scenario.takeover_fraction is not None
        else None
    )
    intervals = tuple(
        current.t_capture_us - previous.t_capture_us
        for previous, current in zip(events, events[1:], strict=False)
    )
    expectations = ScenarioExpectations(
        provenance=DataProvenance.SYNTHETIC,
        headline_evaluation_allowed=False,
        event_intervals_us=intervals,
        takeover_at_us=takeover_at,
        windows=window_expectations,
    )
    framed = build_framed_streams(
        events,
        faults=scenario.faults,
        max_frame_bytes=config.max_frame_bytes,
    )
    return ScenarioBundle(
        name=name,
        events=events,
        feature_windows=tuple(windows),
        scores=scores,
        expectations=expectations,
        framed_streams=framed,
    )
