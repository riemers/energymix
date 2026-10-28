import re

from energymix.helpers import HELPERS


def test_helper_name_gives_its_entity_id():
    # HA maakt de entity_id uit de naam; klopt die niet, dan vindt de add-on
    # de helper niet en maakt hij hem bij elke start opnieuw aan (_2, _3, ...)
    for h in HELPERS:
        slug = re.sub(r"[^a-z0-9]+", "_", h.create["name"].lower()).strip("_")
        assert h.entity_id == f"{h.entity_id.split('.')[0]}.{slug}"
