import unittest

from radar_comercial.gioval_ui import (
    PROFILE_KEY,
    PROFILE_LIST_KEY,
    PROFILE_SELECTOR_KEY,
    add_session_profile,
    clear_profile_work,
    profile_names,
)


class GiovalProfileTests(unittest.TestCase):
    def test_profiles_are_scoped_to_each_session(self):
        first = {}
        second = {}

        self.assertEqual(profile_names(first), ["Giovanna"])
        first["_gioval_new_profile"] = "Harim"
        add_session_profile(first, "gioval_autenticado")

        self.assertEqual(first[PROFILE_KEY], "Harim")
        self.assertEqual(profile_names(second), ["Giovanna"])
        self.assertNotIn("Harim", second[PROFILE_LIST_KEY])

    def test_profile_switch_clears_work_but_keeps_login_and_profile(self):
        state = {
            PROFILE_LIST_KEY: ["Giovanna", "Harim"],
            PROFILE_KEY: "Harim",
            PROFILE_SELECTOR_KEY: "Harim",
            "gioval_autenticado": True,
            "c_nombre": "Dato del perfil anterior",
            "orden_texto": "Pedido en edición",
        }

        clear_profile_work(state, "gioval_autenticado")

        self.assertTrue(state["gioval_autenticado"])
        self.assertEqual(state[PROFILE_KEY], "Harim")
        self.assertEqual(state[PROFILE_LIST_KEY], ["Giovanna", "Harim"])
        self.assertNotIn("c_nombre", state)
        self.assertNotIn("orden_texto", state)

    def test_profile_names_are_trimmed_and_case_insensitive(self):
        state = {PROFILE_LIST_KEY: ["Giovanna", "  HARIM ", "harim", "  "]}

        self.assertEqual(profile_names(state), ["Giovanna", "HARIM"])


if __name__ == "__main__":
    unittest.main()
