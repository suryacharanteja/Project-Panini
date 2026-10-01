from copy import deepcopy

import pytest

from backend.capabilities.desktop_visual import VisualAction, ground_element, same_frame
from backend.panini.contracts import Fault
from backend.tests.panini.test_desktop_visual import Pipe


def snapshot():
    state = Pipe().snapshot()
    state["observation"]["elements"] = [
        {
            "element_id": "control",
            "automation_id": "small",
            "name": "Small",
            "type": "ControlType.Button",
            "enabled": True,
            "bounds": {"x": -60, "y": 20, "width": 14, "height": 18},
        }
    ]
    return state


def test_anchor_is_resolved_without_guessing_from_model_coordinates():
    state = snapshot()
    action = VisualAction(kind="click", explanation="Click small", element_id="control", x=99, y=99)
    grounded = ground_element(action, state)
    assert (grounded.x, grounded.y) == (23, 14)
    assert action.x == 99


@pytest.mark.parametrize("change", ["bounds", "name", "enabled", "element_id", "automation_id", "type"])
def test_anchor_change_rejected_even_when_screenshot_pixels_are_identical(change):
    before = snapshot()
    after = deepcopy(before)
    after["observation"]["elements"][0][change] = None
    action = VisualAction(kind="click", explanation="Click small", element_id="control")
    assert not same_frame(before, after, action)


@pytest.mark.parametrize("mutate", ["missing", "duplicate", "disabled", "outside"])
def test_invalid_anchors_are_not_silently_replaced_with_coordinate_clicks(mutate):
    state = snapshot()
    rows = state["observation"]["elements"]
    if mutate == "missing":
        rows.clear()
    elif mutate == "duplicate":
        rows.append(deepcopy(rows[0]))
    elif mutate == "disabled":
        rows[0]["enabled"] = False
    else:
        rows[0]["bounds"]["x"] = 300
    with pytest.raises(Fault):
        ground_element(VisualAction(kind="click", explanation="Click", element_id="control"), state)
