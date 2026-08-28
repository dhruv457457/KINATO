"""
An opening line is SPOKEN ALOUD to a real customer, and nothing downstream
fills in template slots.

On a real recovery call the agent said: "Hi Dhruv, this is [Your Name] from
[Your Company]..." - reading the brackets out loud - because the plan prompt
never told the model who was calling, so it helpfully emitted placeholders.

The prompt now supplies the real business name (or explicitly says there
isn't one). But a prompt is a request, not a guarantee, so this is the
structural check.
"""
import pytest

from app.services.discovery_agent import contains_placeholder


@pytest.mark.parametrize(
    "line",
    [
        "Hi Dhruv, this is [Your Name] from [Your Company], and I noticed you left items in your cart",
        "Hello, this is {agent_name} calling",
        "Hi, this is <name> from <company>",
        "Hello, this is Your Company calling about your order",
        "Hi, I'm calling from Acme about your cart",
    ],
)
def test_placeholder_lines_are_rejected(line):
    assert contains_placeholder(line), f"placeholder slipped through: {line!r}"


@pytest.mark.parametrize(
    "line",
    [
        "Hi Dhruv, I noticed you were checking out a jacket and wanted to help you finish up.",
        "Hi there! I wanted to help you finish your order.",
        "Hi Asha, your cart is still saved if you'd like to complete it.",
        "Hello, calling about the order you started earlier today.",
    ],
)
def test_genuine_lines_are_allowed(line):
    assert not contains_placeholder(line), f"real line wrongly rejected: {line!r}"


def test_guard_handles_empty_input():
    assert not contains_placeholder("")
    assert not contains_placeholder(None)
