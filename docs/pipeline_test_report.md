# Pipeline Test Report

Generated: 2026-03-24 22:12  
Images: one representative (first non-aug) per dish folder  
Total dishes: 38

Legend: red=mixed_bowl, green=single_dish, gray=discarded  
Annotated images saved to data/debug_crops/{dish_name}.jpg

---

## Results

| Dish | Image | Crops | crop_type | Top-1 | Score | Correct? |
|------|-------|-------|-----------|-------|-------|----------|
| beijing_roast_duck | 0.9070694364694276.jpg | 4 | single_dish | beijing_roast_duck | 0.8863 | ✓ |
| black_bone_chicken_soup | 000000.jpg | 1 | mixed_bowl | base=congee, protein=chicken | — | — |
| boiled_chicken | 000000.jpg | 6 | single_dish | boiled_chicken | 0.8790 | ✓ |
| boiled_fish_with_picked_cabbage_and_chili | 0.36413798559808375.jpg | 4 | single_dish | fish_filets_in_hot_chili_oil | 0.8236 | ✗ ⚠ |
| boiled_shredded_pork_in_chili_oil | 000000.jpg | 1 | single_dish | marinated_egg | 0.7265 | ✗ ⚠ |
| braised_beef | 000000.jpg | 2 | single_dish | black_bone_chicken_soup | 0.8074 | ✗ ⚠ |
| braised_beef_noodle | 000000.jpg | 3 | single_dish | marinated_egg | 0.8334 | ✗ ⚠ |
| braised_pork | 000000.jpg | 3 | single_dish | roast_pork | 0.8457 | ✗ ⚠ |
| broccoli_with_oyster_sauce | 000000.jpg | 5 | single_dish | chicken_braised_with_brown_sauce | 0.8341 | ✗ ⚠ |
| chicken_braised_with_brown_sauce | 000000.jpg | 2 | mixed_bowl | base=congee, protein=pork | — | — |
| chongqing_hot_and_sour_rice_noodles | 000000.jpg | 2 | single_dish | chongqing_hot_and_sour_rice_noodles | 0.8937 | ✓ |
| cola_chicken_wings | 0.23700688645497336.jpg | 2 | mixed_bowl | base=brown_rice, protein=chicken | — | — |
| deep_fried_dough_sticks | 000000.jpg | 5 | single_dish | deep_fried_dough_sticks | 0.9040 | ✓ |
| double_cooked_pork_slices | 000000.jpg | 3 | single_dish | double_cooked_pork_slices | 0.9308 | ✓ |
| dumplings | 000000.jpg | 8 | single_dish | dumplings | 0.8217 | ✓ |
| fish_filets_in_hot_chili_oil | 000013.jpg | 2 | single_dish | boiled_shredded_pork_in_chili_oil | 0.8626 | ✗ ⚠ |
| fried_rice | 000000.jpg | 4 | single_dish | rice | 0.8566 | ✗ ⚠ |
| fried_sweet_and_sour_tenderloin | 000000.jpg | 4 | single_dish | fried_sweet_and_sour_tenderloin | 0.9022 | ✓ |
| hot_pot | hot_pot_christmas.jpeg | 14 | single_dish | dumplings | 0.8432 | ✗ ⚠ |
| japanese_curry | 424F49E7-20AA-4DF2-9276-3AA2D5084113_1_105_c.jpeg | 4 | single_dish | poached_egg | 0.8258 | ✗ ⚠ |
| kung_pao_chicken | 000011.jpg | 3 | single_dish | boiled_chicken | 0.8508 | ✗ ⚠ |
| mapo_tofu | 000000.jpg | 2 | mixed_bowl | base=brown_rice, protein=tofu | — | — |
| marinated_egg | 000000.jpg | 6 | single_dish | marinated_egg | 0.8268 | ✓ |
| noodles_with_egg_and_tomato | 000000.jpg | 2 | single_dish | steamed_egg_custard | 0.8013 | ✗ ⚠ |
| poached_egg | 000000.jpg | 4 | single_dish | sweet_and_sour_spareribs | 0.8143 | ✗ ⚠ |
| rice | 000022.jpg | 1 | mixed_bowl | base=white_rice, protein=tofu | — | — |
| roast_pork | 0.9604666846845913.jpg | 1 | single_dish | beijing_roast_duck | 0.7137 | ✗ ⚠ |
| saute_vegetable | 000000.jpg | 6 | single_dish | saute_vegetable | 0.8216 | ✓ |
| scrambled_egg_with_tomato | 000000.jpg | 3 | mixed_bowl | base=congee, protein=chicken | — | — |
| shredded_pork_and_green_pepper | 000000.jpg | 5 | single_dish | roast_pork | 0.8239 | ✗ ⚠ |
| sirloin_tomatoes | 000000.jpg | 3 | mixed_bowl | protein=beef | — | — |
| spicy_crayfish | 000000.jpg | 1 | single_dish | fried_sweet_and_sour_tenderloin | 0.8060 | ✗ ⚠ |
| spicy_pot | 000000.jpg | 1 | — | (all discarded) | — | — |
| steamed_bun_stuffed | 000000.jpg | 16 | single_dish | steamed_bun_stuffed | 0.8583 | ✓ |
| steamed_chicken_with_chili_sauce | 0.8017027472775525.jpg | 3 | mixed_bowl | base=brown_rice, protein=tofu | — | — |
| steamed_egg_custard | 000000.jpg | 3 | single_dish | steamed_egg_custard | 0.8136 | ✓ |
| sweet_and_sour_spareribs | 000000.jpg | 3 | single_dish | sweet_and_sour_spareribs | 0.9117 | ✓ |
| yu_shiang_shredded_pork | 0.41580218208391617.jpg | 2 | single_dish | yu_shiang_shredded_pork | 0.8031 | ✓ |

---

## Flagged: correct answer not top-1

- boiled_fish_with_picked_cabbage_and_chili: expected top-1=boiled_fish_with_picked_cabbage_and_chili, got fish_filets_in_hot_chili_oil (0.8236)
- boiled_shredded_pork_in_chili_oil: expected top-1=boiled_shredded_pork_in_chili_oil, got marinated_egg (0.7265)
- braised_beef: expected top-1=braised_beef, got black_bone_chicken_soup (0.8074)
- braised_beef_noodle: expected top-1=braised_beef_noodle, got marinated_egg (0.8334)
- braised_pork: expected top-1=braised_pork, got roast_pork (0.8457)
- broccoli_with_oyster_sauce: expected top-1=broccoli_with_oyster_sauce, got chicken_braised_with_brown_sauce (0.8341)
- fish_filets_in_hot_chili_oil: expected top-1=fish_filets_in_hot_chili_oil, got boiled_shredded_pork_in_chili_oil (0.8626)
- fried_rice: expected top-1=fried_rice, got rice (0.8566)
- hot_pot: expected top-1=hot_pot, got dumplings (0.8432)
- japanese_curry: expected top-1=japanese_curry, got poached_egg (0.8258)
- kung_pao_chicken: expected top-1=kung_pao_chicken, got boiled_chicken (0.8508)
- noodles_with_egg_and_tomato: expected top-1=noodles_with_egg_and_tomato, got steamed_egg_custard (0.8013)
- poached_egg: expected top-1=poached_egg, got sweet_and_sour_spareribs (0.8143)
- roast_pork: expected top-1=roast_pork, got beijing_roast_duck (0.7137)
- shredded_pork_and_green_pepper: expected top-1=shredded_pork_and_green_pepper, got roast_pork (0.8239)
- spicy_crayfish: expected top-1=spicy_crayfish, got fried_sweet_and_sour_tenderloin (0.806)

---

*16 dish(es) flagged*