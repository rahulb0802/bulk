from unittest import TestCase

from macro.scrape import parse_nutrition_label

# Trimmed EatSmart / NetNutrition label (Vegetarian Sausage Patties, 2026-10-02).
SAUSAGE_LABEL = """
<div id='nutritionLabel'>
  <td class='cbo_nn_LabelHeader'>Vegetarian Sausage Patties</td>
  <td class='cbo_nn_LabelBottomBorderLabel'>Serving Size:&nbsp;Patty&nbsp;(38g)</td>
  <td class='cbo_nn_LabelDetail'>
    <span style='font-weight: bold;'>Calories</span>
    <span class='cbo_nn_SecondaryNutrient'>70</span>
  </td>
  <td class='cbo_nn_LabelDetailRight'>
    Calories from Fat <span class='cbo_nn_SecondaryNutrient'>23</span>
  </td>
  <span style='font-weight:bold;'>Total Fat</span>
  <span class='cbo_nn_SecondaryNutrient'> 2.5g</span>
  <span>Saturated Fat</span>
  <span class='cbo_nn_SecondaryNutrient'> 0g</span>
  <span style='font-weight:bold;'>Total Carbohydrate</span>
  <span class='cbo_nn_SecondaryNutrient'> 4g</span>
  <span style='font-weight:bold;'>Protein</span>
  <span class='cbo_nn_SecondaryNutrient'> 9g</span>
  <span class='cbo_nn_LabelIngredientsBold'>Ingredients:</span>
  <span class='cbo_nn_LabelIngredients'>Water, Soy Protein Concentrate, Sunflower Oil</span>
</div>
"""

EGGS_LABEL = """
<div id='nutritionLabel'>
  <td class='cbo_nn_LabelBottomBorderLabel'>Serving Size: 1/3 Cup (75g)</td>
  <span style='font-weight: bold;'>Calories</span>
  <span class='cbo_nn_SecondaryNutrient'>150</span>
  Calories from Fat <span class='cbo_nn_SecondaryNutrient'>99</span>
  <span style='font-weight:bold;'>Total Fat</span>
  <span class='cbo_nn_SecondaryNutrient'> 11g</span>
  <span style='font-weight:bold;'>Total Carbohydrate</span>
  <span class='cbo_nn_SecondaryNutrient'> 0g</span>
  <span style='font-weight:bold;'>Protein</span>
  <span class='cbo_nn_SecondaryNutrient'> 9g</span>
</div>
"""


class ParseNutritionLabelTest(TestCase):
    def test_sausage_patty_is_2_5g_fat_not_calories_from_fat(self) -> None:
        n = parse_nutrition_label(SAUSAGE_LABEL)
        self.assertEqual(n.calories, 70)
        self.assertEqual(n.protein_g, 9)
        self.assertEqual(n.carbs_g, 4)
        self.assertEqual(n.fat_g, 2.5)
        self.assertEqual(n.serving_size, "Patty (38g)")

    def test_scrambled_eggs_use_total_fat_not_calories_from_fat(self) -> None:
        n = parse_nutrition_label(EGGS_LABEL)
        self.assertEqual(n.calories, 150)
        self.assertEqual(n.fat_g, 11)
        self.assertNotEqual(n.fat_g, 99)
        self.assertNotEqual(n.calories, 99)
        self.assertEqual(n.protein_g, 9)
        self.assertEqual(n.carbs_g, 0)
