from unittest import TestCase

from macro.settings import load_staples


class LoadStaplesTest(TestCase):
    def test_breakfast_and_all_day_staples(self) -> None:
        items = load_staples()
        names = {(item.meal, item.name) for item in items}
        self.assertIn(("breakfast", "Bagel"), names)
        self.assertIn(("breakfast", "Plain Cream Cheese Cup"), names)
        self.assertIn(("breakfast", "100% Whole Wheat Bread"), names)
        for meal in ("breakfast", "lunch", "dinner"):
            self.assertIn((meal, "Soy Milk"), names)
            self.assertIn((meal, "2% Milk"), names)
            self.assertIn((meal, "Peanut Butter"), names)
            self.assertIn((meal, "Prairie Farms Plain FF Yogurt"), names)

        bagel = next(i for i in items if i.name == "Bagel")
        cream = next(i for i in items if i.name == "Plain Cream Cheese Cup")
        self.assertEqual(bagel.calories() + cream.calories(), 350)
        yogurt = next(i for i in items if i.name == "Prairie Farms Plain FF Yogurt")
        self.assertEqual(yogurt.protein_g(), 12)
        soy = next(i for i in items if i.name == "Soy Milk")
        pb = next(i for i in items if i.name == "Peanut Butter")
        self.assertEqual(soy.calories(), 80)
        self.assertEqual(pb.calories(), 190)
        self.assertTrue(all(i.staple for i in items))
