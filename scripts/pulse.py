#!/usr/bin/env python3
"""Пульс: локальные проверки, состояние кейса и воспроизводимый анализ CSV."""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_CASE_FILES = ("brief.md", "ledger.md", "state.json", "decision.md")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def reject_json_constant(value: str):
    raise ValueError(f"Недопустимое нечисловое значение JSON: {value}")


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_json_constant)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"Не удалось прочитать {path}: {exc}") from exc


def validate_state(state, path: Path) -> list[str]:
    issues = []
    if not isinstance(state, dict):
        return [f"{path}: корень JSON должен быть объектом"]
    for key in ("phase", "updated_at", "decision_owner", "next_action"):
        if key not in state:
            issues.append(f"{path}: нет поля {key}")
        elif not isinstance(state[key], str):
            issues.append(f"{path}: поле {key} должно быть строкой")
    updated_at = state.get("updated_at")
    if isinstance(updated_at, str) and updated_at:
        try:
            date.fromisoformat(updated_at)
        except ValueError:
            issues.append(f"{path}: updated_at должен иметь формат YYYY-MM-DD")
    for key in ("open_questions", "commitments", "review_triggers", "assumptions"):
        if key in state and not isinstance(state[key], list):
            issues.append(f"{path}: поле {key} должно быть списком")
    commitments = state.get("commitments", [])
    if isinstance(commitments, list):
        for index, item in enumerate(commitments, 1):
            if not isinstance(item, dict):
                issues.append(f"{path}: обязательство {index} должно быть объектом")
                continue
            for key in ("action", "owner", "due", "status", "expected_result"):
                if key not in item or not isinstance(item[key], str) or not item[key].strip():
                    issues.append(f"{path}: обязательство {index}, некорректное поле {key}")
            due = item.get("due")
            if isinstance(due, str) and due:
                try:
                    date.fromisoformat(due)
                except ValueError:
                    issues.append(f"{path}: обязательство {index}, срок должен иметь формат YYYY-MM-DD")
    assumptions = state.get("assumptions", [])
    if isinstance(assumptions, list):
        for index, item in enumerate(assumptions, 1):
            if not isinstance(item, dict):
                issues.append(f"{path}: допущение {index} должно быть объектом")
                continue
            for key in ("statement", "owner", "due"):
                if key not in item or not isinstance(item[key], str) or not item[key].strip():
                    issues.append(f"{path}: допущение {index}, некорректное поле {key}")
    return issues


def validate(folder: Path) -> int:
    issues = []
    if not folder.is_dir():
        print(f"Проверка: папка не найдена: {folder}")
        return 1
    for name in REQUIRED_CASE_FILES:
        if not (folder / name).is_file():
            issues.append(f"Нет обязательного файла: {name}")

    ledger = folder / "ledger.md"
    evidence_ids = set()
    if ledger.exists():
        ledger_text = ledger.read_text(encoding="utf-8")
        ids = re.findall(r"^\|\s*([A-Z]+\d+[A-Z0-9_-]*)\s*\|", ledger_text, re.M)
        evidence_ids = {key for key in ids if key.startswith("E")}
        dupes = [key for key, count in Counter(ids).items() if count > 1]
        if dupes:
            issues.append("Повторяются evidence ID: " + ", ".join(dupes))
        for line in ledger_text.splitlines():
            if not re.match(r"^\|\s*E\d", line):
                continue
            cells = line.split("|")
            if len(cells) < 6:
                issues.append(f"Некорректная строка evidence: {line[:80]}")
                continue
            for source in re.findall(r"`([^`]+)`", cells[4]):
                if source.startswith(("http://", "https://")):
                    continue
                if not (folder / source).exists() and not (ROOT / source).exists():
                    issues.append(f"Недоступен источник {source} в {cells[1].strip()}")

    for md in folder.rglob("*.md"):
        if any(part in {".git", "__pycache__"} for part in md.parts):
            continue
        content = md.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", content):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            rel = target.split("#", 1)[0]
            if rel and not (md.parent / rel).exists():
                issues.append(f"Битая ссылка в {md.relative_to(folder)}: {target}")

    decision = folder / "decision.md"
    if decision.exists() and ledger.exists():
        for evidence_id in sorted(set(re.findall(r"\bE\d+[A-Z0-9_-]*\b", decision.read_text(encoding="utf-8")))):
            if evidence_id not in evidence_ids:
                issues.append(f"decision.md ссылается на неизвестный evidence ID: {evidence_id}")

    for path in folder.rglob("*.json"):
        if any(part in {".git", "__pycache__"} for part in path.parts):
            continue
        try:
            state = read_json(path)
            if path.name == "state.json":
                issues.extend(validate_state(state, path.relative_to(folder)))
        except ValueError as exc:
            issues.append(str(exc))

    if issues:
        print("Проверка: найдены проблемы")
        for issue in issues:
            print(f"- {issue}")
        return 1
    print("Проверка пройдена: обязательные файлы, ссылки, evidence ID, JSON и состояние корректны.")
    return 0


def format_item(item) -> str:
    return json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else str(item)


def status(folder: Path) -> int:
    path = folder / "state.json"
    if not path.exists():
        print("Нет state.json. Создайте состояние или запустите pulse.py init <папка>.")
        return 1
    state = read_json(path)
    if not isinstance(state, dict):
        raise ValueError("state.json: корень JSON должен быть объектом")
    print(f"Фаза: {state.get('phase') or 'не задана'}")
    print(f"Обновлено: {state.get('updated_at') or 'не задано'}")
    print(f"Владелец решения: {state.get('decision_owner') or 'не задан'}")
    print(f"Статус выбора: {state.get('decision_status') or 'не задан'}")
    print(f"Следующий шаг: {state.get('next_action') or 'не задан'}")

    overdue = 0
    today = date.today()
    commitments = state.get("commitments", [])
    print(f"Обязательства ({len(commitments)}):")
    for item in commitments:
        if not isinstance(item, dict):
            print("- " + format_item(item))
            continue
        due = item.get("due", "без срока")
        is_overdue = False
        try:
            is_overdue = date.fromisoformat(due) < today and item.get("status", "").lower() not in {"done", "closed", "cancelled"}
        except ValueError:
            pass
        overdue += int(is_overdue)
        marker = " ПРОСРОЧЕНО;" if is_overdue else ""
        print(
            f"- [{item.get('status', 'без статуса')};{marker} срок {due}] "
            f"{item.get('action', 'без действия')} — {item.get('owner', 'без владельца')}; "
            f"результат: {item.get('expected_result', 'не задан')}"
        )
    print(f"Просрочено: {overdue}")

    for heading, key in (
        ("Открытые вопросы", "open_questions"),
        ("Допущения", "assumptions"),
        ("Триггеры пересмотра", "review_triggers"),
    ):
        values = state.get(key, [])
        print(f"{heading} ({len(values)}):")
        for item in values:
            print("- " + format_item(item))
    return 0


def init(folder: Path) -> int:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "sources").mkdir(exist_ok=True)
    for name in REQUIRED_CASE_FILES:
        source = ROOT / "templates" / name
        target = folder / name
        if target.exists():
            print(f"Оставлен существующий файл: {target}")
        else:
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"Создан {target}")
    print("Добавляйте безопасные материалы в sources/ и открывайте папку в AI IDE.")
    return 0


def sample_variance(values: list[float], average: float) -> float | None:
    if len(values) < 2:
        return None
    return sum((value - average) ** 2 for value in values) / (len(values) - 1)


def normal_interval(average: float, variance: float | None, n: int) -> list[float] | None:
    if variance is None:
        return None
    standard_error = math.sqrt(variance / n)
    return [average - 1.96 * standard_error, average + 1.96 * standard_error]


def analyze(args) -> int:
    if args.min_value is not None and args.max_value is not None and args.min_value > args.max_value:
        raise ValueError("--min-value не может быть больше --max-value")
    path = Path(args.csv)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        key_columns = args.key or []
        required = {args.group, args.metric, *key_columns}
        missing = required - set(fieldnames)
        if missing:
            raise ValueError(f"Нет колонок: {', '.join(sorted(missing))}; доступны: {', '.join(fieldnames)}")
        rows = list(reader)
    if not rows:
        raise ValueError("CSV пустой")

    groups: dict[str, list[float]] = {}
    seen_keys = set()
    excluded_non_numeric = 0
    excluded_missing_group = 0
    warnings = []
    for row_number, row in enumerate(rows, 2):
        group = (row.get(args.group) or "").strip()
        if not group:
            excluded_missing_group += 1
            continue
        try:
            value = float(row[args.metric])
            if not math.isfinite(value):
                raise ValueError()
        except (ValueError, TypeError):
            excluded_non_numeric += 1
            continue
        if args.min_value is not None and value < args.min_value:
            raise ValueError(f"Строка {row_number}: {args.metric}={value} меньше {args.min_value}")
        if args.max_value is not None and value > args.max_value:
            raise ValueError(f"Строка {row_number}: {args.metric}={value} больше {args.max_value}")
        if key_columns:
            key = tuple((row.get(column) or "").strip() for column in key_columns)
            if any(not part for part in key):
                raise ValueError(f"Строка {row_number}: пустой ключ наблюдения")
            identity = (*key, group)
            if identity in seen_keys:
                raise ValueError(f"Повтор наблюдения для ключа {key} и группы {group}")
            seen_keys.add(identity)
        groups.setdefault(group, []).append(value)

    if len(groups) != 2:
        raise ValueError(f"Для разницы нужны ровно две непустые группы; найдены: {list(groups)}")
    if excluded_non_numeric:
        warnings.append(f"Исключено строк с нечисловой метрикой: {excluded_non_numeric}")
    if excluded_missing_group:
        warnings.append(f"Исключено строк без группы: {excluded_missing_group}")
    if not key_columns:
        warnings.append("Ключ наблюдения не задан: дубли по периоду/ID не проверялись")
    for name, value in (("unit", args.unit), ("period", args.period), ("observation-unit", args.observation_unit)):
        if not value:
            warnings.append(f"Метаданное --{name} не задано")

    names = sorted(groups)
    stats = {}
    variances = {}
    for name in names:
        values = groups[name]
        average = sum(values) / len(values)
        variance = sample_variance(values, average)
        variances[name] = variance
        stats[name] = {
            "n": len(values),
            "mean": average,
            "min": min(values),
            "max": max(values),
            "sample_stdev": math.sqrt(variance) if variance is not None else None,
            "ci95_normal_approx": normal_interval(average, variance, len(values)) if args.normal_approximation else None,
        }

    a, b = names
    difference = stats[b]["mean"] - stats[a]["mean"]
    difference_ci = None
    if args.normal_approximation and variances[a] is not None and variances[b] is not None:
        standard_error = math.sqrt(variances[a] / len(groups[a]) + variances[b] / len(groups[b]))
        difference_ci = [difference - 1.96 * standard_error, difference + 1.96 * standard_error]
    if args.normal_approximation:
        uncertainty = {
            "status": "estimated" if difference_ci is not None else "insufficient_data",
            "method": "Welch/normal approximation",
            "assumption": "Наблюдения независимы; приближение может быть ненадёжным при малом n",
        }
        if difference_ci is None:
            warnings.append("Для интервала нужно минимум два наблюдения в каждой группе")
    else:
        uncertainty = {
            "status": "not_estimated",
            "reason": "Независимость наблюдений и пользовательский знаменатель не подтверждены; разброс строк не является доверительным интервалом для пользователей",
        }

    result = {
        "input": str(path),
        "rows": len(rows),
        "excluded_non_numeric": excluded_non_numeric,
        "excluded_missing_group": excluded_missing_group,
        "group_column": args.group,
        "metric_column": args.metric,
        "key_columns": key_columns,
        "unit": args.unit or "not_provided",
        "period": args.period or "not_provided",
        "observation_unit": args.observation_unit or "not_provided",
        "allowed_range": [args.min_value, args.max_value],
        "groups": stats,
        "contrast": f"{b} minus {a}",
        "difference": difference,
        "difference_ci95_normal_approx": difference_ci,
        "uncertainty": uncertainty,
        "warnings": warnings,
        "interpretation": "Описательная разница средних строк. Результат не является причинным эффектом или оценкой неопределённости пользовательской метрики без подтверждённого дизайна.",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        output = Path(args.output)
        output.write_text(rendered + "\n", encoding="utf-8")
        print(f"Отчёт сохранён: {output}")
    print(rendered)
    return 0


def economic_number(data, key: str, minimum: float = 0, exclusive: bool = False) -> float:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"Экономический параметр {key} должен быть конечным числом")
    if (exclusive and value <= minimum) or (not exclusive and value < minimum):
        relation = ">" if exclusive else ">="
        raise ValueError(f"Экономический параметр {key} должен быть {relation} {minimum}")
    return float(value)


def economics(args) -> int:
    path = Path(args.input)
    data = read_json(path)
    if not isinstance(data, dict):
        raise ValueError("Экономический вход должен быть JSON-объектом")
    starts = economic_number(data, "starts_per_month", exclusive=True)
    margin = economic_number(data, "margin_per_activation", exclusive=True)
    one_time_cost = economic_number(data, "one_time_cost")
    support_cost = economic_number(data, "monthly_support_cost")
    horizon = economic_number(data, "horizon_months", exclusive=True)
    currency = data.get("currency")
    if not isinstance(currency, str) or not currency.strip():
        raise ValueError("Экономический параметр currency должен быть непустой строкой")
    baseline = data.get("baseline_activation")
    if baseline is not None:
        baseline = economic_number(data, "baseline_activation")
        if baseline > 1:
            raise ValueError("baseline_activation должен быть в диапазоне [0, 1]")
    uplifts = data.get("uplifts")
    if not isinstance(uplifts, list) or not uplifts:
        raise ValueError("uplifts должен быть непустым списком абсолютных приростов доли")

    scenarios = []
    for uplift in uplifts:
        if isinstance(uplift, bool) or not isinstance(uplift, (int, float)) or not math.isfinite(uplift) or not -1 <= uplift <= 1:
            raise ValueError("Каждый uplift должен быть конечным числом в диапазоне [-1, 1]")
        if baseline is not None and not 0 <= baseline + uplift <= 1:
            raise ValueError("baseline_activation + uplift должен оставаться в диапазоне [0, 1]")
        monthly_gross = starts * uplift * margin
        monthly_net = monthly_gross - support_cost
        scenarios.append({
            "absolute_uplift": uplift,
            "uplift_percentage_points": uplift * 100,
            "incremental_activations_per_month": starts * uplift,
            "monthly_gross_value": monthly_gross,
            "monthly_net_after_support": monthly_net,
            "horizon_net_after_one_time_cost": monthly_net * horizon - one_time_cost,
            "payback_months": one_time_cost / monthly_net if monthly_net > 0 else None,
        })
    result = {
        "input": str(path),
        "data_status": data.get("data_status", "not_declared"),
        "currency": currency,
        "assumptions": {
            "starts_per_month": starts,
            "baseline_activation": baseline,
            "margin_per_activation": margin,
            "one_time_cost": one_time_cost,
            "monthly_support_cost": support_cost,
            "horizon_months": horizon,
        },
        "formulas": {
            "monthly_net_after_support": "starts_per_month * absolute_uplift * margin_per_activation - monthly_support_cost",
            "horizon_net_after_one_time_cost": "monthly_net_after_support * horizon_months - one_time_cost",
            "break_even_absolute_uplift": "(one_time_cost / horizon_months + monthly_support_cost) / (starts_per_month * margin_per_activation)",
        },
        "break_even_absolute_uplift": (one_time_cost / horizon + support_cost) / (starts * margin),
        "scenarios": scenarios,
        "interpretation": "Сценарный расчёт, а не прогноз или причинный эффект. Использовать фактические значения только после подтверждения определений, периода и полной стоимости.",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        output = Path(args.output)
        output.write_text(rendered + "\n", encoding="utf-8")
        print(f"Отчёт сохранён: {output}")
    print(rendered)
    return 0


def cohort_list(scenario: dict, key: str, months: int) -> list[tuple[int, float]]:
    cohorts = scenario.get(key, [])
    if not isinstance(cohorts, list):
        raise ValueError(f"{key} должен быть списком когорт")
    result = []
    for item in cohorts:
        if not isinstance(item, dict):
            raise ValueError(f"{key}: каждая когорта должна быть объектом")
        start = item.get("start_month")
        if isinstance(start, bool) or not isinstance(start, int) or not 1 <= start <= months:
            raise ValueError(f"{key}: start_month должен быть целым от 1 до {months}")
        result.append((start, economic_number(item, "numbers")))
    return result


def paid_months(args) -> int:
    """Вклад в валовую прибыль через дополнительные оплаченные месяцы номеров."""
    path = Path(args.input)
    data = read_json(path)
    if not isinstance(data, dict):
        raise ValueError("Вход должен быть JSON-объектом")
    margin = economic_number(data, "margin_per_paid_month", exclusive=True)
    months = data.get("plan_year_months", 12)
    if isinstance(months, bool) or not isinstance(months, int) or months < 1:
        raise ValueError("plan_year_months должен быть положительным целым")
    baseline_profit = economic_number(data, "baseline_gross_profit")
    target_profit = economic_number(data, "target_gross_profit")
    currency = data.get("currency")
    if not isinstance(currency, str) or not currency.strip():
        raise ValueError("currency должен быть непустой строкой")
    scenarios = data.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("scenarios должен быть непустым списком")

    rendered_scenarios = []
    for scenario in scenarios:
        if not isinstance(scenario, dict) or not isinstance(scenario.get("name"), str):
            raise ValueError("Каждый сценарий должен быть объектом с полем name")
        base_churn = economic_number(scenario, "baseline_monthly_churn")
        treated_churn = economic_number(scenario, "treated_monthly_churn")
        if base_churn > 1 or treated_churn > 1:
            raise ValueError("Месячный отток должен быть в диапазоне [0, 1]")
        direct_cost = economic_number(scenario, "monthly_direct_cost")
        cost_start = scenario.get("direct_cost_start_month", 1)
        if isinstance(cost_start, bool) or not isinstance(cost_start, int) or not 1 <= cost_start <= months:
            raise ValueError(f"direct_cost_start_month должен быть целым от 1 до {months}")
        other = scenario.get("other_annual_contribution", 0)
        if isinstance(other, bool) or not isinstance(other, (int, float)) or not math.isfinite(other):
            raise ValueError("other_annual_contribution должен быть конечным числом")

        retention_months = 0.0
        for start, numbers in cohort_list(scenario, "retention_cohorts", months):
            for step in range(1, months - start + 2):
                retention_months += numbers * ((1 - treated_churn) ** step - (1 - base_churn) ** step)
        new_months = 0.0
        for start, numbers in cohort_list(scenario, "new_number_cohorts", months):
            for step in range(0, months - start + 1):
                new_months += numbers * (1 - treated_churn) ** step

        total_months = retention_months + new_months
        direct_costs = direct_cost * (months - cost_start + 1)
        gross = total_months * margin + other
        net = gross - direct_costs
        projected = baseline_profit + net
        item = {
            "name": scenario["name"],
            "additional_paid_months_retention": round(retention_months, 1),
            "additional_paid_months_new_numbers": round(new_months, 1),
            "additional_paid_months_total": round(total_months, 1),
            "gross_contribution": round(gross, 2),
            "direct_costs_in_year": round(direct_costs, 2),
            "net_contribution": round(net, 2),
            "projected_gross_profit": round(projected, 2),
            "gap_to_target": round(target_profit - projected, 2),
            "paid_months_required_for_target": round((target_profit - baseline_profit - other + direct_costs) / margin, 1),
            "max_investment_recovered_within_year": round(max(net, 0.0), 2),
        }
        if "one_time_investment" in scenario:
            investment = economic_number(scenario, "one_time_investment")
            item["one_time_investment"] = investment
            item["investment_recovered_within_year"] = net >= investment
        rendered_scenarios.append(item)

    result = {
        "input": Path(args.input).as_posix(),
        "data_status": data.get("data_status", "not_declared"),
        "currency": currency,
        "assumptions": {
            "margin_per_paid_month": margin,
            "plan_year_months": months,
            "baseline_gross_profit": baseline_profit,
            "target_gross_profit": target_profit,
        },
        "formulas": {
            "retention_paid_months": "sum по когортам и месяцам k=1..(T-start+1): numbers * ((1-treated_churn)^k - (1-baseline_churn)^k)",
            "new_number_paid_months": "sum по когортам и месяцам k=0..(T-start): numbers * (1-treated_churn)^k",
            "net_contribution": "paid_months_total * margin_per_paid_month + other_annual_contribution - monthly_direct_cost * (T - direct_cost_start_month + 1)",
            "paid_months_required_for_target": "(target_gross_profit - baseline_gross_profit - other_annual_contribution + direct_costs_in_year) / margin_per_paid_month",
        },
        "scenarios": rendered_scenarios,
        "interpretation": "Сценарный расчёт на допущениях, а не прогноз. Стоимость разработки учитывается отдельно; деньги после конца планового года в расчёт не входят.",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        output = Path(args.output)
        output.write_text(rendered + "\n", encoding="utf-8")
        print(f"Отчёт сохранён: {output}")
    print(rendered)
    return 0


def sample_size(args) -> int:
    """Число единиц на группу для сравнения двух долей (нормальное приближение)."""
    from statistics import NormalDist

    p1, p2 = args.p1, args.p2
    for name, value in (("p1", p1), ("p2", p2)):
        if not math.isfinite(value) or not 0 < value < 1:
            raise ValueError(f"--{name} должен быть долей строго между 0 и 1")
    if p1 == p2:
        raise ValueError("--p1 и --p2 не должны совпадать: нет разницы для обнаружения")
    if not 0 < args.alpha < 1 or not 0 < args.power < 1:
        raise ValueError("--alpha и --power должны быть между 0 и 1")
    if args.design_effect < 1:
        raise ValueError("--design-effect не может быть меньше 1")
    z_alpha = NormalDist().inv_cdf(1 - args.alpha / 2)
    z_power = NormalDist().inv_cdf(args.power)
    raw = (z_alpha + z_power) ** 2 * (p1 * (1 - p1) + p2 * (1 - p2)) / (p1 - p2) ** 2
    per_group = math.ceil(raw * args.design_effect)
    result = {
        "baseline_share": p1,
        "target_share": p2,
        "absolute_difference_pp": round((p2 - p1) * 100, 4),
        "relative_change": round((p2 - p1) / p1, 4),
        "alpha_two_sided": args.alpha,
        "power": args.power,
        "design_effect": args.design_effect,
        "units_per_group": per_group,
        "units_total": per_group * 2,
        "required_new_units_per_week": math.ceil(per_group * 2 / args.weeks) if args.weeks else None,
        "recruitment_weeks": args.weeks,
        "formula": "n = (z_(1-alpha/2) + z_power)^2 * (p1*(1-p1) + p2*(1-p2)) / (p1-p2)^2 * design_effect",
        "interpretation": "Плановая оценка для двух независимых групп и одного события на единицу. Единица — то, что распределяют случайно (например, семейная группа). Базовая доля должна быть измерена до запуска; расчёт не подтверждает дизайн и не заменяет аналитика.",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        output = Path(args.output)
        output.write_text(rendered + "\n", encoding="utf-8")
        print(f"Отчёт сохранён: {output}")
    print(rendered)
    return 0


def analysis_command() -> str:
    return (
        "python scripts/pulse.py analyze demo/case/data/retention.csv "
        "--group group --metric retained --key week --unit share "
        "--period \"week 4 retention; weekly aggregates\" "
        "--observation-unit \"week-group aggregate; not users\" "
        "--min-value 0 --max-value 1 --output demo/case/analysis.json"
    )


def economics_command() -> str:
    return "python scripts/pulse.py economics demo/case/economics.json --output demo/case/economics_analysis.json"


def demo() -> int:
    print("Пульс / два демонстрационных кейса")
    print("А) Кейс по брифу партнёра: demo/family-connect (README.md, strategy.md, decision.md).")
    print("   python scripts/pulse.py paid-months demo/family-connect/economics/paid_months.json")
    print("   python scripts/pulse.py sample-size --p1 0.12 --p2 0.09 --weeks 6")
    print("   python scripts/pulse.py validate demo/family-connect")
    print("   python scripts/pulse.py status demo/family-connect")
    print("Б) Кейс eSIM на открытых страницах и скриншоте: demo/case.")
    print("1) Прочитайте demo/case/README.md и decision.md.")
    print("2) Воспроизведите анализ CSV: " + analysis_command())
    print("3) Пересчитайте экономику: " + economics_command())
    print("4) Проверьте кейс: python scripts/pulse.py validate demo/case")
    print("5) Проверьте состояние: python scripts/pulse.py status demo/case")
    print("6) Продолжите в AI IDE по AGENTS.md; синтетические данные не являются доказательством спроса или причинного эффекта.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Локальные проверки и воспроизводимая аналитика Пульса")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("validate", help="Проверить файлы, ссылки, evidence ID и состояние")
    p.add_argument("folder", type=Path)
    p = sub.add_parser("status", help="Показать состояние и просроченные обязательства")
    p.add_argument("folder", type=Path)
    p = sub.add_parser("init", help="Создать каркас нового кейса")
    p.add_argument("folder", type=Path)
    p = sub.add_parser("analyze", help="Посчитать описательную разницу средних в CSV (ровно 2 группы)")
    p.add_argument("csv")
    p.add_argument("--group", required=True, help="Колонка с именем группы")
    p.add_argument("--metric", required=True, help="Числовая колонка")
    p.add_argument("--key", action="append", help="Колонка ключа наблюдения; повторите для составного ключа")
    p.add_argument("--unit", help="Подтверждённая единица метрики")
    p.add_argument("--period", help="Период и окно метрики")
    p.add_argument("--observation-unit", help="Что представляет строка: пользователь, неделя-группа и т. п.")
    p.add_argument("--min-value", type=float, help="Минимальное допустимое значение метрики")
    p.add_argument("--max-value", type=float, help="Максимальное допустимое значение метрики")
    p.add_argument("--normal-approximation", action="store_true", help="Показать приближённый интервал только при обоснованной независимости наблюдений")
    p.add_argument("--output", help="Путь JSON-отчёта")
    p = sub.add_parser("economics", help="Пересчитать сценарии экономики из JSON-допущений")
    p.add_argument("input", help="JSON с параметрами расчёта")
    p.add_argument("--output", help="Путь JSON-отчёта")
    p = sub.add_parser("paid-months", help="Вклад в валовую прибыль через дополнительные оплаченные месяцы (JSON-сценарии)")
    p.add_argument("input", help="JSON со сценариями удержания и новых номеров")
    p.add_argument("--output", help="Путь JSON-отчёта")
    p = sub.add_parser("sample-size", help="Число единиц на группу для сравнения двух долей")
    p.add_argument("--p1", type=float, required=True, help="Базовая доля события, например 0.12")
    p.add_argument("--p2", type=float, required=True, help="Доля при наименьшем важном улучшении, например 0.09")
    p.add_argument("--alpha", type=float, default=0.05, help="Двусторонний уровень значимости (по умолчанию 0.05)")
    p.add_argument("--power", type=float, default=0.8, help="Мощность (по умолчанию 0.8)")
    p.add_argument("--design-effect", type=float, default=1.0, help="Поправка на кластеры или повторные события (>= 1)")
    p.add_argument("--weeks", type=int, help="Недель на набор: покажет нужный поток новых единиц в неделю")
    p.add_argument("--output", help="Путь JSON-отчёта")
    sub.add_parser("demo", help="Показать сценарий демонстрации")
    args = parser.parse_args()
    try:
        if args.command == "validate":
            return validate(args.folder)
        if args.command == "status":
            return status(args.folder)
        if args.command == "init":
            return init(args.folder)
        if args.command == "analyze":
            return analyze(args)
        if args.command == "economics":
            return economics(args)
        if args.command == "paid-months":
            return paid_months(args)
        if args.command == "sample-size":
            return sample_size(args)
        if args.command == "demo":
            return demo()
    except (OSError, ValueError, csv.Error) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
