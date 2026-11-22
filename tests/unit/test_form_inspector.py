"""Label association for the HTML inspector (spec §13.2/§13.3).

Real ATS markup nests the control one or more elements inside its ``<label>``;
Lever's application form is ``<label><div>Full name</div><div><input></div>
</label>``. An inspector that only looks at the direct parent classifies those
required fields as unrecognised, so this file pins the implicit association.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.domain.models.browser import FormModel
from app.domain.services.field_classifier import AUTOCOMPLETE, KIND_FALLBACKS, RULES
from app.domain.services.form_inspector import inspect_html

SETTINGS_ROUTE = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "features"
    / "settings"
    / "SettingsRoute.tsx"
)

URL = "https://example.test/apply"


def _model(html: str) -> FormModel:
    return inspect_html(html, session_id="session-1", url=URL)


def test_input_nested_inside_its_label_is_recognised():
    model = _model(
        """
        <form action="/apply" method="post">
          <li class="application-question">
            <label>
              <div class="application-label">Full name</div>
              <div class="application-field">
                <input type="text" name="name" required>
              </div>
            </label>
          </li>
          <li class="application-question">
            <label>
              <div class="application-label">Current company</div>
              <div class="application-field">
                <input type="text" name="org" required>
              </div>
            </label>
          </li>
        </form>
        """
    )
    by_name = {field.name_attr: field for field in model.fields}
    assert by_name["name"].label == "Full name"
    assert by_name["name"].classification.key == "full_name"
    assert by_name["org"].label == "Current company"
    assert by_name["org"].classification.key == "current_employer"


def test_a_deeply_nested_control_does_not_steal_a_sibling_question():
    model = _model(
        """
        <form action="/apply" method="post">
          <fieldset>
            <label><div>Email</div><div><input id="e" name="email" type="email"></div></label>
            <label><div>Pronouns</div><div><select name="pronouns">
              <option value="">Decline</option><option value="she/her">she/her</option>
            </select></div></label>
          </fieldset>
        </form>
        """
    )
    by_name = {field.name_attr: field for field in model.fields}
    assert by_name["email"].label == "Email"
    assert by_name["pronouns"].label == "Pronouns"
    # A demographic label still wins over the section it sits in.
    assert by_name["pronouns"].classification.field_class == "blocked_demographic"


def test_a_wrapped_select_does_not_carry_its_option_text():
    model = _model(
        """
        <form action="/apply" method="post">
          <label>Country
            <select name="country">
              <option>United Kingdom</option>
              <option>France</option>
            </select>
          </label>
        </form>
        """
    )
    field = next(field for field in model.fields if field.name_attr == "country")
    assert field.label == "Country"
    assert "France" not in (field.label or "")


def test_a_label_wrapping_several_controls_labels_none_of_them():
    model = _model(
        """
        <form action="/apply" method="post">
          <label>Contact
            <input id="first" name="first" type="text">
            <input id="second" name="second" type="text">
          </label>
        </form>
        """
    )
    by_id = {field.id_attr: field for field in model.fields}
    assert by_id["first"].label is None
    assert by_id["second"].label is None


def test_hidden_inputs_do_not_make_a_label_ambiguous():
    model = _model(
        """
        <form action="/apply" method="post">
          <label>LinkedIn
            <input type="hidden" name="source" value="apply">
            <input name="urls[LinkedIn]" type="text">
          </label>
        </form>
        """
    )
    field = next(field for field in model.fields if field.name_attr == "urls[LinkedIn]")
    assert field.label == "LinkedIn"


def test_a_for_linked_label_still_wins_over_a_wrapping_one():
    model = _model(
        """
        <form action="/apply" method="post">
          <label for="em">Email address</label>
          <label>Other <input id="em" name="em" type="text"></label>
        </form>
        """
    )
    field = next(field for field in model.fields if field.name_attr == "em")
    assert field.label == "Email address"


CARD_CHOICES = """
<form action="/apply" method="post">
  <div class="section page-centered application-form">
    <h4>Work Eligibility</h4>
    <ul>
      <li class="application-question custom-question">
        <div>
          <div class="application-label full-width multiple-select">
            <div class="text">Are you eligible to work in the United States?</div>
          </div>
          <div class="application-field full-width">
            <ul data-qa="checkboxes">
              <li><label><input type="checkbox" name="cards[1][field0]" value="Yes" />
                <span class="application-answer-alternative">Yes</span></label></li>
              <li><label><input type="checkbox" name="cards[1][field0]" value="No" />
                <span class="application-answer-alternative">No</span></label></li>
            </ul>
          </div>
        </div>
      </li>
    </ul>
  </div>
</form>
"""


def test_a_card_question_names_the_choice_group_it_sits_above():
    """Lever's cards put the question beside the control list, not in a label."""
    model = _model(CARD_CHOICES)
    boxes = [field for field in model.fields if field.kind == "checkbox"]
    assert [field.label for field in boxes] == [
        "Are you eligible to work in the United States?"
    ] * 2
    for field in boxes:
        assert field.classification.field_class == "user_supplied_answer"
        assert field.classification.key == "work_authorization"
    # Each box is addressable on its own, so an answer cannot land on the other.
    assert len({field.selector for field in boxes}) == 2
    assert [box.options[0].label for box in boxes] == ["Yes", "No"]


def test_a_section_heading_is_not_used_as_a_question():
    model = _model(
        """
        <form action="/apply" method="post">
          <div class="section">
            <h4>Links</h4>
            <ul>
              <li><label><input type="checkbox" name="newsletter" value="Newsletter">
                <span>Newsletter</span></label></li>
            </ul>
          </div>
        </form>
        """
    )
    field = next(field for field in model.fields if field.name_attr == "newsletter")
    assert field.label == "Newsletter"


def test_current_location_is_a_profile_field():
    """"Current location" is contact data, not a survey question."""
    model = _model(
        """
        <form action="/apply" method="post">
          <li class="application-question" data-qa="structured-contact-location-question">
            <label>
              <div class="application-label">Current location </div>
              <div class="application-field">
                <input class="location-input" id="location-input" name="location"
                       type="text" maxlength="100">
                <input id="selected-location" type="hidden" name="selectedLocation">
              </div>
            </label>
          </li>
        </form>
        """
    )
    field = next(field for field in model.fields if field.id_attr == "location-input")
    assert field.label == "Current location"
    assert field.classification.key == "location"
    assert field.classification.field_class == "fillable_profile"


def test_an_age_range_survey_question_stays_demographic():
    model = _model(
        """
        <form action="/apply" method="post">
          <li class="application-question">
            <div class="application-label multiple-choice">
              <div class="text">What is your age range?</div>
            </div>
            <div class="application-field">
              <ul>
                <li><label><input type="radio" name="age" value="18-20">
                  <span>18-20</span></label></li>
                <li><label><input type="radio" name="age" value="21-29">
                  <span>21-29</span></label></li>
              </ul>
            </div>
          </li>
        </form>
        """
    )
    group = next(field for field in model.fields if field.kind == "radio_group")
    assert group.label == "What is your age range?"
    assert group.classification.field_class == "blocked_demographic"


def test_a_card_question_names_a_radio_group_and_keeps_its_options():
    model = _model(
        """
        <form action="/apply" method="post">
          <div class="section">
            <div>
              <div class="application-label multiple-choice">
                <div class="text">Which office do you prefer?</div>
              </div>
              <div class="application-field">
                <ul>
                  <li><label><input type="radio" name="office" value="london">
                    <span>London</span></label></li>
                  <li><label><input type="radio" name="office" value="berlin">
                    <span>Berlin</span></label></li>
                </ul>
              </div>
            </div>
          </div>
        </form>
        """
    )
    group = next(field for field in model.fields if field.kind == "radio_group")
    assert group.label == "Which office do you prefer?"
    assert [option.label for option in group.options] == ["London", "Berlin"]


def test_a_video_link_is_a_profile_url_not_an_open_question():
    """Lever's "Video Link URL" asks for a link the user already has stored."""
    model = _model(
        """
        <form action="/apply" method="post">
          <li class="application-question">
            <label>
              <div class="application-label">Video Link URL</div>
              <div class="application-field">
                <input type="text" name="urls[Video]">
              </div>
            </label>
          </li>
        </form>
        """
    )
    field = next(field for field in model.fields if field.name_attr == "urls[Video]")
    assert field.classification.field_class == "fillable_profile"
    assert field.classification.key == "video_url"


def test_every_answerable_classifier_key_has_a_settings_field() -> None:
    """Recognition and the answer book must not drift apart.

    A key the classifier can produce but the settings screen cannot store means
    a recognised field is left blank for want of somewhere to put the answer
    (Lever's required "Full name" was exactly that). ``date_of_birth`` is
    recognised only so it can be refused, so it needs no field.
    """
    answerable = {key for _, key in AUTOCOMPLETE.values() if key}
    answerable |= {key for _pattern, _class, key, _evidence in RULES if key}
    answerable |= {key for _class, key, _confidence in KIND_FALLBACKS.values() if key}
    answerable.discard("date_of_birth")

    edited = set(
        re.findall(r'key: "([a-z0-9_]+)"', SETTINGS_ROUTE.read_text(encoding="utf-8"))
    )
    assert answerable <= edited, sorted(answerable - edited)
