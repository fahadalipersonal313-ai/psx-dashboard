"""Isolated tests for explicit scenario math; no prices or portfolio are changed."""

from decimal import Decimal
import math
import unittest

from research_sizing import size_scenario


def scenario(**changes):
    assumptions = dict(entry=100, stop=90, target=120, capital=100000,
                       cash=50000, loss_budget=1000, fee_bps_per_side=0,
                       entry_slippage_bps=0, exit_slippage_bps=0,
                       fixed_round_trip_cost=0, position_cap_pct=25,
                       median_daily_volume=100000, participation_pct=1,
                       lot_size=1)
    assumptions.update(changes)
    return size_scenario(**assumptions)


class ScenarioSizingTests(unittest.TestCase):
    def test_no_costs_exact_risk_boundary(self):
        result = scenario()
        self.assertEqual(result["status"], "sized")
        self.assertEqual(result["shares"], 100)
        self.assertEqual(result["cash_required"], 10000)
        self.assertEqual(result["modeled_stop_loss"], 1000)
        self.assertEqual(result["target_net_gain"], 2000)
        self.assertEqual(result["net_reward_risk"], 2)
        self.assertEqual(result["binding_constraints"], ["loss_budget"])

    def test_costs_use_slipped_execution_prices_and_full_fixed_reserve(self):
        result = scenario(fee_bps_per_side=100, entry_slippage_bps=200,
                          exit_slippage_bps=300, fixed_round_trip_cost=50)
        unit = result["per_unit_costs"]
        self.assertAlmostEqual(unit["entry_execution_price"], 102)
        self.assertAlmostEqual(unit["entry_fee"], 1.02)
        self.assertAlmostEqual(unit["entry_debit"], 103.02)
        self.assertAlmostEqual(unit["stop_proceeds"], 86.427)
        self.assertAlmostEqual(unit["target_proceeds"], 115.236)
        self.assertAlmostEqual(unit["modeled_stop_loss"], 16.593)
        self.assertEqual(result["shares"], 57)
        self.assertAlmostEqual(result["cash_required"], 5922.14)
        self.assertAlmostEqual(result["modeled_stop_loss"], 995.801)
        self.assertAlmostEqual(result["target_net_gain"], 646.312)
        self.assertAlmostEqual(result["net_reward_risk"], 646.312 / 995.801)

    def test_cash_binds_after_reserving_full_fixed_cost(self):
        result = scenario(cash=1049, fixed_round_trip_cost=50)
        self.assertEqual(result["shares"], 9)
        self.assertEqual(result["cash_required"], 950)
        self.assertEqual(result["binding_constraints"], ["cash"])

    def test_loss_budget_binds_after_fixed_cost(self):
        result = scenario(loss_budget=149, fixed_round_trip_cost=50)
        self.assertEqual(result["shares"], 9)
        self.assertEqual(result["modeled_stop_loss"], 140)
        self.assertEqual(result["binding_constraints"], ["loss_budget"])

    def test_position_cap_includes_costs_and_fixed_reserve(self):
        result = scenario(position_cap_pct=1, fixed_round_trip_cost=50,
                          fee_bps_per_side=100)
        self.assertEqual(result["shares"], 9)
        self.assertEqual(result["cash_required"], 959)
        self.assertEqual(result["binding_constraints"], ["position_cap"])

    def test_volume_binds_with_percentage_units(self):
        result = scenario(median_daily_volume=999, participation_pct=1)
        self.assertEqual(result["shares"], 9)
        self.assertEqual(result["binding_constraints"], ["volume_participation"])

    def test_missing_volume_withholds_quantity_and_totals(self):
        result = scenario(median_daily_volume=None)
        self.assertEqual(result["status"], "withheld")
        for key in ("shares", "cash_required", "modeled_stop_loss",
                    "target_net_gain", "net_reward_risk"):
            self.assertIsNone(result[key], key)
        self.assertEqual(result["binding_constraints"], ["unverified_volume"])
        self.assertIsNone(result["share_limits"]["volume_participation"])
        self.assertEqual(result["per_unit_costs"]["entry_debit"], 100)

    def test_known_zero_volume_is_zero_quantity_not_missing_volume(self):
        result = scenario(median_daily_volume=0)
        self.assertEqual(result["status"], "zero_shares")
        self.assertEqual(result["shares"], 0)
        self.assertEqual(result["binding_constraints"], ["volume_participation"])

    def test_zero_trade_has_no_fixed_cost(self):
        for changes in ({"cash": 0}, {"loss_budget": 0}, {"position_cap_pct": 0},
                        {"participation_pct": 0}, {"cash": 49}, {"loss_budget": 49},
                        {"cash": 50}, {"loss_budget": 50}):
            with self.subTest(changes=changes):
                result = scenario(fixed_round_trip_cost=50, **changes)
                self.assertEqual(result["status"], "zero_shares")
                self.assertEqual(result["shares"], 0)
                for key in ("cash_required", "modeled_stop_loss", "target_net_gain"):
                    self.assertEqual(result[key], 0)
                self.assertIsNone(result["net_reward_risk"])

    def test_round_down_to_lot_for_every_limit(self):
        for changes, binding in [({"cash": 9550}, "cash"),
                                 ({"loss_budget": 950}, "loss_budget"),
                                 ({"position_cap_pct": 9.5}, "position_cap"),
                                 ({"median_daily_volume": 9500}, "volume_participation")]:
            with self.subTest(binding=binding):
                result = scenario(lot_size=25, **changes)
                self.assertEqual(result["shares"], 75)
                self.assertEqual(result["share_limits"][binding], 75)
                self.assertIn(binding, result["binding_constraints"])

    def test_smaller_than_lot_returns_zero(self):
        result = scenario(cash=2499, lot_size=25)
        self.assertEqual(result["shares"], 0)
        self.assertEqual(result["cash_required"], 0)

    def test_all_tied_constraints_reported(self):
        result = scenario(cash=10000, position_cap_pct=10, median_daily_volume=10000)
        self.assertEqual(result["shares"], 100)
        self.assertEqual(set(result["binding_constraints"]),
                         {"cash", "loss_budget", "position_cap", "volume_participation"})

    def test_binary_float_boundary_does_not_lose_a_share(self):
        result = scenario(entry=0.1, stop=0.09, target=0.12, cash=0.3,
                          loss_budget=1, position_cap_pct=100)
        self.assertEqual(result["shares"], 3)
        self.assertEqual(result["cash_required"], 0.3)

    def test_fixed_cost_boundary_keeps_exact_last_lot(self):
        result = scenario(cash=1050, loss_budget=150, fixed_round_trip_cost=50, lot_size=5)
        self.assertEqual(result["shares"], 10)
        self.assertEqual(result["cash_required"], 1050)
        self.assertEqual(result["modeled_stop_loss"], 150)

    def test_higher_costs_never_increase_quantity_or_reward_risk(self):
        for cost in ("fee_bps_per_side", "entry_slippage_bps", "exit_slippage_bps",
                     "fixed_round_trip_cost"):
            previous = scenario()
            for amount in (1, 10, 50, 100):
                with self.subTest(cost=cost, amount=amount):
                    current = scenario(**{cost: amount})
                    self.assertLessEqual(current["shares"], previous["shares"])
                    self.assertLessEqual(current["net_reward_risk"], previous["net_reward_risk"])
                    self.assertGreaterEqual(current["per_unit_costs"]["modeled_stop_loss"],
                                            previous["per_unit_costs"]["modeled_stop_loss"])
                    self.assertLessEqual(current["per_unit_costs"]["target_net_gain"],
                                         previous["per_unit_costs"]["target_net_gain"])
                    previous = current

    def test_target_less_profitable_after_costs_is_not_hidden(self):
        result = scenario(target=100.1, fee_bps_per_side=100)
        self.assertGreater(result["shares"], 0)
        self.assertLess(result["target_net_gain"], 0)
        self.assertLess(result["net_reward_risk"], 0)
        self.assertTrue(any("no positive net gain" in text for text in result["warnings"]))

    def test_decimal_inputs_and_integer_valued_lot(self):
        result = scenario(entry=Decimal("100"), lot_size=Decimal("25.0"))
        self.assertEqual(result["shares"], 100)
        self.assertIsInstance(result["shares"], int)

    def test_explicit_assumptions_are_preserved(self):
        result = scenario(fee_bps_per_side=17, entry_slippage_bps=9,
                          exit_slippage_bps=21, fixed_round_trip_cost=40)
        for key, value in [("fee_bps_per_side", 17), ("entry_slippage_bps", 9),
                           ("exit_slippage_bps", 21), ("fixed_round_trip_cost", 40)]:
            self.assertEqual(result["assumptions"][key], value)
        self.assertTrue(any("not a guaranteed maximum" in s for s in result["warnings"]))

    def test_all_returned_values_are_finite_for_large_but_representable_inputs(self):
        result = scenario(entry=1e150, stop=9e149, target=1.2e150,
                          capital=1e155, cash=5e154, loss_budget=1e151,
                          median_daily_volume=1e9)
        self.assertGreater(result["shares"], 0)
        for key in ("cash_required", "modeled_stop_loss", "target_net_gain", "net_reward_risk"):
            self.assertTrue(math.isfinite(result[key]))

    def test_never_exceeds_any_constraint_across_scenario_grid(self):
        for fee in (0, 13, 100):
            for fixed in (0, 51, 2000):
                for lot in (1, 25, 1000):
                    with self.subTest(fee=fee, fixed=fixed, lot=lot):
                        result = scenario(fee_bps_per_side=fee,
                                          fixed_round_trip_cost=fixed, lot_size=lot,
                                          entry_slippage_bps=17, exit_slippage_bps=23,
                                          cash=5000, position_cap_pct=4,
                                          median_daily_volume=7500)
                        self.assertEqual(result["shares"] % lot, 0)
                        self.assertLessEqual(result["cash_required"], 4000)
                        self.assertLessEqual(result["modeled_stop_loss"], 1000)
                        self.assertLessEqual(result["shares"], 75)


class InvalidScenarioTests(unittest.TestCase):
    FIELDS = ("entry", "stop", "target", "capital", "cash", "loss_budget",
              "fee_bps_per_side", "entry_slippage_bps", "exit_slippage_bps",
              "fixed_round_trip_cost", "position_cap_pct", "median_daily_volume",
              "participation_pct", "lot_size")

    def test_every_field_rejects_bool_strings_nonfinite_and_complex(self):
        for field in self.FIELDS:
            for value in (True, False, "1", float("nan"), float("inf"),
                          float("-inf"), Decimal("NaN"), 1 + 0j):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        scenario(**{field: value})

    def test_none_only_valid_for_missing_volume(self):
        for field in set(self.FIELDS) - {"median_daily_volume"}:
            with self.subTest(field=field), self.assertRaises(ValueError):
                scenario(**{field: None})

    def test_negative_fields_are_rejected(self):
        for field in self.FIELDS:
            with self.subTest(field=field), self.assertRaises(ValueError):
                scenario(**{field: -1})

    def test_zero_and_reversed_prices_rejected(self):
        for changes in ({"entry": 0}, {"stop": 0}, {"target": 0},
                        {"stop": 100}, {"stop": 101}, {"target": 100}, {"target": 99}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                scenario(**changes)

    def test_capital_cash_and_risk_budget_bounds(self):
        for changes in ({"capital": 0}, {"cash": 100001}, {"loss_budget": 100001}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                scenario(**changes)

    def test_rate_upper_bounds(self):
        for field in ("fee_bps_per_side", "entry_slippage_bps", "exit_slippage_bps"):
            for value in (10000, 10001):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    scenario(**{field: value})
        for field in ("position_cap_pct", "participation_pct"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                scenario(**{field: 100.01})

    def test_zero_fractional_or_negative_lot_is_rejected(self):
        for lot in (0, -1, 0.5, 1.5):
            with self.subTest(lot=lot), self.assertRaises(ValueError):
                scenario(lot_size=lot)

    def test_missing_volume_does_not_skip_other_validation(self):
        with self.assertRaises(ValueError):
            scenario(median_daily_volume=None, fee_bps_per_side=-1)

    def test_derived_overflow_fails_closed(self):
        with self.assertRaises(ValueError):
            scenario(entry=1e308, stop=9e307, target=1.1e308,
                     capital=1.7e308, cash=1.7e308, loss_budget=1e307,
                     entry_slippage_bps=9000, fee_bps_per_side=9000)


if __name__ == "__main__":
    unittest.main()
