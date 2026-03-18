"""
DataProcessPlugin — 数据加载、分析、保存

从 web/adaptive_agent.py 的 data_process 工具迁移而来,
适配 UnifiedAgent 插件体系。
"""

import json
import os
from typing import Any, Dict, List

from app.core.agent.base import AgentPlugin


class DataProcessPlugin(AgentPlugin):
    """Provides data loading, analysis, and export capabilities (pandas-backed)."""

    @property
    def name(self) -> str:
        return "DataProcess"

    @property
    def description(self) -> str:
        return "Load, inspect, and save tabular data files (CSV, Excel, JSON)."

    def get_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "load_data",
                "func": self.load_data,
                "description": "Load a tabular data file and return shape, columns, and a preview. "
                "Supports .csv, .xlsx, .xls, .json.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "filepath": {
                            "type": "STRING",
                            "description": "Path to the data file.",
                        }
                    },
                    "required": ["filepath"],
                },
            },
            {
                "name": "query_data",
                "func": self.query_data,
                "description": "Load a data file and run a pandas expression on it. "
                               "Returns the first 20 result rows.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "filepath": {
                            "type": "STRING",
                            "description": "Path to the data file.",
                        },
                        "expression": {
                            "type": "STRING",
                            "description": "A pandas DataFrame expression using only 'df', "
                                           "e.g. \"df[df['age'] > 30].describe()\"."
                        }
                    },
                    "required": ["filepath", "expression"],
                },
            },
            {
                "name": "describe_data",
                "func": self.describe_data,
                "description": "Return a comprehensive statistical summary of a dataset: "
                               "numeric stats, missing value counts, and top categorical values.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "filepath": {
                            "type": "STRING",
                            "description": "Path to the data file."
                        }
                    },
                    "required": ["filepath"]
                }
            },
            {
                "name": "suggest_questions",
                "func": self.suggest_questions,
                "description": "Inspect a dataset's schema and return suggested analysis questions "
                               "to guide further exploration (distributions, trends, groupings, "
                               "data quality).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "filepath": {
                            "type": "STRING",
                            "description": "Path to the data file."
                        }
                    },
                    "required": ["filepath"]
                }
            },
            {
                "name": "save_data",
                "func": self.save_data,
                "description": "Save data (provided as JSON rows) to a file. "
                "Supports .csv, .xlsx, .json output.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "filepath": {
                            "type": "STRING",
                            "description": "Destination file path.",
                        },
                        "data_json": {
                            "type": "STRING",
                            "description": "JSON string of records (list of dicts).",
                        },
                    },
                    "required": ["filepath", "data_json"],
                },
            },
            {
                "name": "analyze_trends",
                "func": self.analyze_trends,
                "description": "Perform a time-series trend analysis on a data file: "
                               "moving averages, growth rates, peak/trough detection, "
                               "and periodicity hints. Requires a date column and a numeric value column.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "filepath": {
                            "type": "STRING",
                            "description": "Path to the data file (CSV/Excel/JSON).",
                        },
                        "date_col": {
                            "type": "STRING",
                            "description": "Name of the column containing dates/timestamps.",
                        },
                        "value_col": {
                            "type": "STRING",
                            "description": "Name of the numeric column to analyse.",
                        },
                        "freq": {
                            "type": "STRING",
                            "description": (
                                "Optional resample frequency: 'D' (daily), 'W' (weekly), "
                                "'ME' (month-end), 'QE' (quarter-end). Defaults to auto-detect."
                            ),
                        },
                    },
                    "required": ["filepath", "date_col", "value_col"],
                },
            },
        ]

    # ------------------------------------------------------------------

    @staticmethod
    def _load_df(filepath: str):
        """Internal helper — load file into a pandas DataFrame."""
        import pandas as pd

        ext = os.path.splitext(filepath)[1].lower()
        if ext == ".csv":
            return pd.read_csv(filepath)
        elif ext in (".xlsx", ".xls"):
            return pd.read_excel(filepath)
        elif ext == ".json":
            return pd.read_json(filepath)
        else:
            raise ValueError(f"Unsupported file format: {ext}")

    def load_data(self, filepath: str) -> str:
        """Load a data file and return shape + preview."""
        try:
            df = self._load_df(filepath)
            preview = df.head(10).to_string(index=False)
            return (
                f"Shape: {df.shape[0]} rows × {df.shape[1]} columns\n"
                f"Columns: {', '.join(df.columns)}\n\n"
                f"Preview:\n{preview}"
            )
        except Exception as exc:
            return f"Error loading data: {exc}"

    # Patterns that could enable file I/O or code execution via the eval sandbox
    _EXPR_BLOCKLIST = [
        "read_csv", "read_excel", "read_json", "read_parquet", "read_table",
        "to_csv", "to_excel", "to_json", "to_parquet",
        "os.", "subprocess", "open(", "__import__", "import ",
        "exec(", "eval(", "system(", "popen(", "getattr", "setattr",
    ]

    def query_data(self, filepath: str, expression: str) -> str:
        """Load data and evaluate a pandas expression (df-only namespace)."""
        expr_lower = expression.lower()
        for pattern in self._EXPR_BLOCKLIST:
            if pattern in expr_lower:
                return f"Error: Expression contains blocked pattern '{pattern}'."
        try:
            df = self._load_df(filepath)
            # Only 'df' is exposed — pandas module is NOT injected to block file I/O.
            result = eval(expression, {"__builtins__": {}}, {"df": df})
            if hasattr(result, "to_string"):
                return str(result.head(20).to_string())
            return str(result)
        except Exception as exc:
            return f"Error evaluating expression: {exc}"

    def describe_data(self, filepath: str) -> str:
        """Return a comprehensive statistical summary of the dataset."""
        try:
            import pandas as pd
            df = self._load_df(filepath)
            lines = [
                f"Shape: {df.shape[0]} rows × {df.shape[1]} columns",
                f"Columns: {', '.join(str(c) for c in df.columns)}",
                "",
                "## Numeric Summary",
                df.describe().to_string(),
            ]
            missing = df.isnull().sum()
            missing = missing[missing > 0]
            lines.append("")
            lines.append("## Missing Values")
            if missing.empty:
                lines.append("None.")
            else:
                for col, cnt in missing.items():
                    pct = cnt / len(df) * 100
                    lines.append(f"  {col}: {cnt} missing ({pct:.1f}%)")
            cat_cols = df.select_dtypes(include=["object", "category"]).columns.tolist()
            if cat_cols:
                lines.append("")
                lines.append("## Categorical Columns (top 5 values each)")
                for col in cat_cols[:5]:
                    top = df[col].value_counts().head(5)
                    lines.append(f"  {col}: {dict(top)}")
            return "\n".join(lines)
        except Exception as exc:
            return f"Error describing data: {exc}"

    def suggest_questions(self, filepath: str) -> str:
        """Suggest analysis questions based on the dataset's schema."""
        try:
            df = self._load_df(filepath)
            cols = df.columns.tolist()
            questions: list[str] = [
                f"Based on this dataset ({df.shape[0]} rows × {df.shape[1]} columns), "
                "here are suggested analysis questions:"
            ]
            num_cols = df.select_dtypes(include="number").columns.tolist()
            if num_cols:
                questions.append("\n### 分布与统计")
                for col in num_cols[:3]:
                    questions.append(f"- '{col}' 的分布如何？是否存在异常值（需要 3σ 检测）？")
                if len(num_cols) >= 2:
                    questions.append(
                        f"- '{num_cols[0]}' 和 '{num_cols[1]}' 之间是否存在相关性？"
                    )
            date_kws = ["date", "time", "日期", "时间", "year", "month", "week"]
            date_cols = [
                c for c in cols
                if any(kw in str(c).lower() for kw in date_kws)
            ]
            if date_cols:
                questions.append("\n### 时间趋势")
                questions.append(f"- 数据随时间（列: '{date_cols[0]}'）如何变化？是否有周期性规律？")
                if num_cols:
                    questions.append(f"- '{num_cols[0]}' 的月度/周度趋势是什么？")
            cat_cols = df.select_dtypes(include=["object", "category"]).columns.tolist()
            if cat_cols:
                questions.append("\n### 分组分析")
                for col in cat_cols[:2]:
                    if num_cols:
                        questions.append(
                            f"- 哪个 '{col}' 分组的 '{num_cols[0]}' 最高/最低？"
                        )
                    questions.append(f"- '{col}' 各类别的分布比例是什么？")
            missing_cols = [c for c in cols if df[c].isnull().any()]
            if missing_cols:
                questions.append("\n### 数据质量")
                questions.append(
                    f"- 为什么 {', '.join(missing_cols[:3])} 存在缺失值？应如何处理？"
                )
            if df.shape[0] > 1000:
                questions.append("\n### 规模考量")
                questions.append(
                    f"- 数据共 {df.shape[0]} 行，初步探索时是否需要抽样？抽样比例建议？"
                )
            return "\n".join(questions)
        except Exception as exc:
            return f"Error generating questions: {exc}"

    @staticmethod
    def save_data(filepath: str, data_json: str) -> str:
        """Save JSON records to a file."""
        try:
            import pandas as pd

            records = json.loads(data_json)
            df = pd.DataFrame(records)

            os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
            ext = os.path.splitext(filepath)[1].lower()
            if ext == ".csv":
                df.to_csv(filepath, index=False)
            elif ext in (".xlsx", ".xls"):
                df.to_excel(filepath, index=False)
            elif ext == ".json":
                df.to_json(filepath, orient="records", force_ascii=False, indent=2)
            else:
                return f"Unsupported output format: {ext}"

            return f"Data saved to {filepath} ({len(df)} rows)."
        except Exception as exc:
            return f"Error saving data: {exc}"

    def analyze_trends(
        self,
        filepath: str,
        date_col: str,
        value_col: str,
        freq: str = "",
    ) -> str:
        """Run a time-series trend analysis on user data."""
        try:
            import pandas as pd
            import math

            df = self._load_df(filepath)

            # ── validate columns ────────────────────────────────────────
            missing = [c for c in (date_col, value_col) if c not in df.columns]
            if missing:
                available = ", ".join(df.columns)
                return (
                    f"Error: 找不到列 {missing}。"
                    f"数据集中可用的列：{available}"
                )

            # ── parse dates + sort ──────────────────────────────────────
            df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
            df = df.dropna(subset=[date_col]).sort_values(date_col)

            series: pd.Series = pd.to_numeric(df[value_col], errors="coerce").dropna()
            df = df.loc[series.index]
            df = df.set_index(date_col)[value_col].astype(float)

            if len(df) < 2:
                return "Error: 有效数据点不足，至少需要 2 条记录。"

            # ── auto-detect frequency ───────────────────────────────────
            if not freq:
                span_days = (df.index[-1] - df.index[0]).days
                n = len(df)
                avg_gap = span_days / max(n - 1, 1)
                if avg_gap <= 1.5:
                    freq = "D"
                elif avg_gap <= 10:
                    freq = "W"
                elif avg_gap <= 45:
                    freq = "ME"
                else:
                    freq = "QE"

            # ── resample ────────────────────────────────────────────────
            try:
                resampled = df.resample(freq).mean().dropna()
            except Exception:
                resampled = df

            if len(resampled) < 2:
                resampled = df

            # ── basic stats ─────────────────────────────────────────────
            total_min = float(resampled.min())
            total_max = float(resampled.max())
            total_mean = float(resampled.mean())
            first_val = float(resampled.iloc[0])
            last_val = float(resampled.iloc[-1])
            overall_change = last_val - first_val
            overall_pct = (overall_change / abs(first_val) * 100) if first_val != 0 else float("nan")

            # ── moving average (3-period) ────────────────────────────────
            ma = resampled.rolling(window=min(3, len(resampled)), min_periods=1).mean()

            # ── period-over-period growth ────────────────────────────────
            pct_change = resampled.pct_change().dropna() * 100
            avg_growth = float(pct_change.mean()) if len(pct_change) > 0 else 0.0
            max_growth = float(pct_change.max()) if len(pct_change) > 0 else 0.0
            min_growth = float(pct_change.min()) if len(pct_change) > 0 else 0.0

            # ── peak / trough ────────────────────────────────────────────
            peak_idx = resampled.idxmax()
            trough_idx = resampled.idxmin()

            # ── simple trend direction via linear regression slope ───────
            x = list(range(len(resampled)))
            y = list(resampled)
            n = len(x)
            mean_x = sum(x) / n
            mean_y = sum(y) / n
            num = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
            den = sum((xi - mean_x) ** 2 for xi in x)
            slope = num / den if den != 0 else 0.0
            trend_word = "上升📈" if slope > 0 else ("下降📉" if slope < 0 else "平稳➡️")

            # ── volatility (coefficient of variation) ───────────────────
            std_val = float(resampled.std())
            cv = (std_val / abs(total_mean) * 100) if total_mean != 0 else 0.0
            volatility_word = "高波动" if cv > 30 else ("中等波动" if cv > 10 else "低波动")

            # ── build output ─────────────────────────────────────────────
            freq_names = {"D": "日", "W": "周", "ME": "月", "QE": "季度"}
            freq_label = freq_names.get(freq, freq)
            lines = [
                f"## 📊 趋势分析：{value_col}（按{freq_label}）",
                f"**数据范围**：{df.index[0].strftime('%Y-%m-%d')} → "
                f"{df.index[-1].strftime('%Y-%m-%d')}（共 {len(df)} 条记录，"
                f"重采样后 {len(resampled)} 个{freq_label}）",
                "",
                "### 整体概况",
                f"| 指标 | 值 |",
                f"|------|------|",
                f"| 总体趋势 | {trend_word} |",
                f"| 起始值 | {first_val:,.2f} |",
                f"| 结束值 | {last_val:,.2f} |",
                f"| 总变化 | {overall_change:+,.2f}"
                + (f"（{overall_pct:+.1f}%）" if not math.isnan(overall_pct) else "") + " |",
                f"| 平均值 | {total_mean:,.2f} |",
                f"| 最高值 | {total_max:,.2f}（{peak_idx.strftime('%Y-%m-%d')}） |",
                f"| 最低值 | {total_min:,.2f}（{trough_idx.strftime('%Y-%m-%d')}） |",
                f"| 波动性 | {volatility_word}（变异系数 {cv:.1f}%） |",
                "",
                "### 期间环比增长",
                f"| 平均增长率 | 最大增幅 | 最大跌幅 |",
                f"|------------|----------|----------|",
                f"| {avg_growth:+.2f}% | {max_growth:+.2f}% | {min_growth:+.2f}% |",
                "",
                "### 近期走势（最近 5 个周期）",
            ]

            tail = resampled.tail(5)
            for ts, val in tail.items():
                ma_val = float(ma.get(ts, val))
                lines.append(
                    f"- {ts.strftime('%Y-%m-%d')}：{val:,.2f}"
                    f"（3期均线 {ma_val:,.2f}）"
                )

            # ── anomaly hint ─────────────────────────────────────────────
            z_scores = (resampled - total_mean) / (std_val if std_val > 0 else 1)
            anomalies = z_scores[z_scores.abs() > 2.5]
            if not anomalies.empty:
                lines.append("")
                lines.append("### ⚠️ 异常数据点（偏离均值 >2.5σ）")
                for ts, z in anomalies.items():
                    val = float(resampled[ts])
                    lines.append(f"- {ts.strftime('%Y-%m-%d')}：{val:,.2f}（{z:+.1f}σ）")

            return "\n".join(lines)
        except Exception as exc:
            return f"Error analyzing trends: {exc}"
