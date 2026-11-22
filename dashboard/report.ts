export type ModuleKind = "quiz" | "assign";

export type PassStatus =
  | "available"
  | "needs_update"
  | "not_numeric"
  | "no_grades";

export interface CourseActivityReport {
  course_module_id: number;
  module: ModuleKind;
  name: string;
  observed_submitters: number | null;
  observed_submission_share: number | null;
}

export interface CourseGradeItemReport {
  grade_item_id: number;
  course_module_id: number;
  name: string;
  hidden: boolean;
  graded_learners: number;
  pass_status: PassStatus;
  passed_learners: number | null;
  pass_rate: number | null;
}

export interface CourseLogWindow {
  from: number;
  to: number;
  standard_log_available: boolean;
}

export interface CourseReport {
  snapshot_id: string;
  course_id: number;
  observed_at: number;
  cohort_size: number;
  log_window: CourseLogWindow;
  pass_percent: number;
  activities: CourseActivityReport[];
  grade_items: CourseGradeItemReport[];
}

export interface ModelSplitSummary {
  cohort_size: number;
  labeled: number;
  passed: number;
  unlabeled: Record<string, number>;
}

export interface ModelBinaryMetrics {
  brier: number;
  accuracy_at_half: number;
}

export interface ModelHistoryBucket {
  labeled: number;
  brier: number | null;
  accuracy_at_half: number | null;
}

export interface ModelEvaluationReport {
  pass_percent: number;
  train: ModelSplitSummary;
  test: ModelSplitSummary;
  train_prevalence: number;
  test_metrics: {
    logistic: ModelBinaryMetrics;
    prevalence: ModelBinaryMetrics;
  };
  test_by_history: {
    with_history: ModelHistoryBucket;
    no_history: ModelHistoryBucket;
  };
}

export type ParseResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: string };

const FORBIDDEN_LEARNER_KEYS = new Set([
  "userid",
  "user_id",
  "learner_id",
  "learnerid",
  "student_id",
  "studentid",
  "users",
  "learners",
  "students",
  "username",
  "email",
  "fullname",
]);

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function hasExactKeys(
  obj: Record<string, unknown>,
  expectedKeys: readonly string[],
): boolean {
  for (const key of Object.keys(obj)) {
    if (FORBIDDEN_LEARNER_KEYS.has(key.toLowerCase())) {
      return false;
    }
  }
  const actualKeys = Object.keys(obj);
  if (actualKeys.length !== expectedKeys.length) {
    return false;
  }
  for (const key of expectedKeys) {
    if (!Object.prototype.hasOwnProperty.call(obj, key)) {
      return false;
    }
  }
  return true;
}

function isNonNegativeInt(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isSafeInteger(value) &&
    value >= 0
  );
}

function isUnixTimestamp(value: unknown): value is number {
  return isNonNegativeInt(value) && value <= 8_640_000_000_000;
}

function isBoundedRatio(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isFinite(value) &&
    value >= 0 &&
    value <= 1
  );
}

function isPassPercent(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isFinite(value) &&
    value >= 0 &&
    value <= 100
  );
}

function isNonEmptySafeString(value: unknown, maxLen = 300): value is string {
  return (
    typeof value === "string" &&
    value.trim().length > 0 &&
    value.length <= maxLen &&
    !/[\u0000-\u001F]/.test(value)
  );
}

function isSha256Hex(value: unknown): value is string {
  return typeof value === "string" && /^[a-fA-F0-9]{64}$/.test(value);
}

export function parseCourseReportJson(rawText: string): ParseResult<CourseReport> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawText);
  } catch {
    return {
      ok: false,
      error: "Tệp không phải là JSON hợp lệ. Vui lòng chọn tệp báo cáo khóa học đúng định dạng.",
    };
  }

  if (!isPlainObject(parsed)) {
    return {
      ok: false,
      error: "Cấu trúc báo cáo khóa học không hợp lệ (dữ liệu gốc phải là một đối tượng JSON).",
    };
  }

  const rootKeys = [
    "snapshot_id",
    "course_id",
    "observed_at",
    "cohort_size",
    "log_window",
    "pass_percent",
    "activities",
    "grade_items",
  ] as const;

  if (!hasExactKeys(parsed, rootKeys)) {
    return {
      ok: false,
      error: "Tệp JSON không khớp với lược đồ Báo cáo khóa học hoặc chứa trường không được phép.",
    };
  }

  if (!isSha256Hex(parsed.snapshot_id)) {
    return {
      ok: false,
      error: "Trường snapshot_id phải là mã băm SHA-256 gồm 64 ký tự thập lục phân.",
    };
  }

  if (!isNonNegativeInt(parsed.course_id)) {
    return {
      ok: false,
      error: "Trường course_id phải là số nguyên không âm.",
    };
  }

  if (!isUnixTimestamp(parsed.observed_at)) {
    return {
      ok: false,
      error: "Trường observed_at phải là dấu thời gian Unix (giây nguyên không âm).",
    };
  }

  if (!isNonNegativeInt(parsed.cohort_size)) {
    return {
      ok: false,
      error: "Trường cohort_size phải là số nguyên không âm.",
    };
  }

  const cohortSize = parsed.cohort_size;

  if (!isPassPercent(parsed.pass_percent)) {
    return {
      ok: false,
      error: "Trường pass_percent phải nằm trong khoảng từ 0 đến 100.",
    };
  }

  if (!isPlainObject(parsed.log_window)) {
    return {
      ok: false,
      error: "Trường log_window không hợp lệ.",
    };
  }

  if (
    !hasExactKeys(parsed.log_window, [
      "from",
      "to",
      "standard_log_available",
    ]) ||
    !isUnixTimestamp(parsed.log_window.from) ||
    !isUnixTimestamp(parsed.log_window.to) ||
    parsed.log_window.from >= parsed.log_window.to ||
    typeof parsed.log_window.standard_log_available !== "boolean"
  ) {
    return {
      ok: false,
      error: "Thông tin cửa sổ nhật ký (log_window) không hợp lệ.",
    };
  }

  if (!Array.isArray(parsed.activities)) {
    return {
      ok: false,
      error: "Danh sách hoạt động (activities) phải là một mảng.",
    };
  }

  const activities: CourseActivityReport[] = [];
  for (let i = 0; i < parsed.activities.length; i += 1) {
    const item = parsed.activities[i];
    if (
      !isPlainObject(item) ||
      !hasExactKeys(item, [
        "course_module_id",
        "module",
        "name",
        "observed_submitters",
        "observed_submission_share",
      ])
    ) {
      return {
        ok: false,
        error: `Mục hoạt động ở vị trí #${i + 1} không đúng cấu trúc yêu cầu.`,
      };
    }

    if (!isNonNegativeInt(item.course_module_id)) {
      return {
        ok: false,
        error: `Hoạt động #${i + 1} có course_module_id không hợp lệ.`,
      };
    }

    if (item.module !== "quiz" && item.module !== "assign") {
      return {
        ok: false,
        error: `Hoạt động #${i + 1} có loại mô-đun không được hỗ trợ (chỉ chấp nhận quiz hoặc assign).`,
      };
    }

    if (!isNonEmptySafeString(item.name)) {
      return {
        ok: false,
        error: `Hoạt động #${i + 1} có tên không hợp lệ.`,
      };
    }

    const submitters = item.observed_submitters;
    const share = item.observed_submission_share;

    if (submitters !== null && (!isNonNegativeInt(submitters) || submitters > cohortSize)) {
      return {
        ok: false,
        error: `Hoạt động #${i + 1} có số người nộp quan sát được không hợp lệ.`,
      };
    }

    if (share !== null && !isBoundedRatio(share)) {
      return {
        ok: false,
        error: `Hoạt động #${i + 1} có tỷ lệ nộp bài ngoài phạm vi [0, 1].`,
      };
    }

    activities.push({
      course_module_id: item.course_module_id,
      module: item.module,
      name: item.name,
      observed_submitters: submitters,
      observed_submission_share: share,
    });
  }

  if (!Array.isArray(parsed.grade_items)) {
    return {
      ok: false,
      error: "Danh sách hạng mục điểm (grade_items) phải là một mảng.",
    };
  }

  const validPassStatuses = new Set<PassStatus>([
    "available",
    "needs_update",
    "not_numeric",
    "no_grades",
  ]);

  const gradeItems: CourseGradeItemReport[] = [];
  for (let i = 0; i < parsed.grade_items.length; i += 1) {
    const item = parsed.grade_items[i];
    if (
      !isPlainObject(item) ||
      !hasExactKeys(item, [
        "grade_item_id",
        "course_module_id",
        "name",
        "hidden",
        "graded_learners",
        "pass_status",
        "passed_learners",
        "pass_rate",
      ])
    ) {
      return {
        ok: false,
        error: `Hạng mục điểm ở vị trí #${i + 1} không đúng cấu trúc yêu cầu.`,
      };
    }

    if (
      !isNonNegativeInt(item.grade_item_id) ||
      !isNonNegativeInt(item.course_module_id) ||
      typeof item.hidden !== "boolean" ||
      !isNonEmptySafeString(item.name) ||
      !isNonNegativeInt(item.graded_learners) ||
      item.graded_learners > cohortSize
    ) {
      return {
        ok: false,
        error: `Hạng mục điểm #${i + 1} có thông tin định danh hoặc số lượng được chấm không hợp lệ.`,
      };
    }

    if (
      typeof item.pass_status !== "string" ||
      !validPassStatuses.has(item.pass_status as PassStatus)
    ) {
      return {
        ok: false,
        error: `Hạng mục điểm #${i + 1} có trạng thái đạt (pass_status) không hợp lệ.`,
      };
    }

    const passedLearners = item.passed_learners;
    const passRate = item.pass_rate;

    if (
      passedLearners !== null &&
      (!isNonNegativeInt(passedLearners) ||
        passedLearners > item.graded_learners)
    ) {
      return {
        ok: false,
        error: `Hạng mục điểm #${i + 1} có số học viên đạt (passed_learners) không hợp lệ.`,
      };
    }

    if (passRate !== null && !isBoundedRatio(passRate)) {
      return {
        ok: false,
        error: `Hạng mục điểm #${i + 1} có tỷ lệ đạt (pass_rate) ngoài phạm vi [0, 1].`,
      };
    }

    gradeItems.push({
      grade_item_id: item.grade_item_id,
      course_module_id: item.course_module_id,
      name: item.name,
      hidden: item.hidden,
      graded_learners: item.graded_learners,
      pass_status: item.pass_status as PassStatus,
      passed_learners: passedLearners,
      pass_rate: passRate,
    });
  }

  return {
    ok: true,
    data: {
      snapshot_id: parsed.snapshot_id.toLowerCase(),
      course_id: parsed.course_id,
      observed_at: parsed.observed_at,
      cohort_size: cohortSize,
      log_window: {
        from: parsed.log_window.from,
        to: parsed.log_window.to,
        standard_log_available: parsed.log_window.standard_log_available,
      },
      pass_percent: parsed.pass_percent,
      activities,
      grade_items: gradeItems,
    },
  };
}

function parseSplitSummary(
  raw: unknown,
  label: string,
): ParseResult<ModelSplitSummary> {
  if (
    !isPlainObject(raw) ||
    !hasExactKeys(raw, ["cohort_size", "labeled", "passed", "unlabeled"])
  ) {
    return {
      ok: false,
      error: `Tập dữ liệu ${label} không đúng cấu trúc yêu cầu.`,
    };
  }

  if (
    !isNonNegativeInt(raw.cohort_size) ||
    !isNonNegativeInt(raw.labeled) ||
    !isNonNegativeInt(raw.passed) ||
    raw.labeled > raw.cohort_size ||
    raw.passed > raw.labeled
  ) {
    return {
      ok: false,
      error: `Số lượng mẫu trong tập ${label} không hợp lệ (cần đảm bảo 0 <= đạt <= có nhãn <= quy mô đoàn hệ).`,
    };
  }

  if (!isPlainObject(raw.unlabeled)) {
    return {
      ok: false,
      error: `Trường unlabeled trong tập ${label} phải là một đối tượng thống kê theo lý do.`,
    };
  }

  const unlabeled: Record<string, number> = {};
  for (const [reason, count] of Object.entries(raw.unlabeled)) {
    if (
      FORBIDDEN_LEARNER_KEYS.has(reason.toLowerCase()) ||
      !isNonEmptySafeString(reason, 80) ||
      !/^[a-z0-9_:-]+$/i.test(reason) ||
      !isNonNegativeInt(count) ||
      count > raw.cohort_size
    ) {
      return {
        ok: false,
        error: `Thống kê mẫu thiếu nhãn trong tập ${label} có mã lý do hoặc số đếm không hợp lệ.`,
      };
    }
    unlabeled[reason] = count;
  }

  return {
    ok: true,
    data: {
      cohort_size: raw.cohort_size,
      labeled: raw.labeled,
      passed: raw.passed,
      unlabeled,
    },
  };
}

function parseBinaryMetrics(
  raw: unknown,
  label: string,
): ParseResult<ModelBinaryMetrics> {
  if (
    !isPlainObject(raw) ||
    !hasExactKeys(raw, ["brier", "accuracy_at_half"]) ||
    !isBoundedRatio(raw.brier) ||
    !isBoundedRatio(raw.accuracy_at_half)
  ) {
    return {
      ok: false,
      error: `Chỉ số đánh giá (${label}) không hợp lệ hoặc nằm ngoài phạm vi [0, 1].`,
    };
  }
  return {
    ok: true,
    data: {
      brier: raw.brier,
      accuracy_at_half: raw.accuracy_at_half,
    },
  };
}

function parseHistoryBucket(
  raw: unknown,
  label: string,
): ParseResult<ModelHistoryBucket> {
  if (
    !isPlainObject(raw) ||
    !hasExactKeys(raw, ["labeled", "brier", "accuracy_at_half"]) ||
    !isNonNegativeInt(raw.labeled)
  ) {
    return {
      ok: false,
      error: `Nhóm lịch sử (${label}) không đúng cấu trúc yêu cầu.`,
    };
  }

  if (raw.brier !== null && !isBoundedRatio(raw.brier)) {
    return {
      ok: false,
      error: `Điểm Brier của nhóm (${label}) phải là null hoặc số trong đoạn [0, 1].`,
    };
  }

  if (raw.accuracy_at_half !== null && !isBoundedRatio(raw.accuracy_at_half)) {
    return {
      ok: false,
      error: `Độ chính xác của nhóm (${label}) phải là null hoặc số trong đoạn [0, 1].`,
    };
  }

  return {
    ok: true,
    data: {
      labeled: raw.labeled,
      brier: raw.brier,
      accuracy_at_half: raw.accuracy_at_half,
    },
  };
}

export function parseModelEvaluationJson(
  rawText: string,
): ParseResult<ModelEvaluationReport> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawText);
  } catch {
    return {
      ok: false,
      error: "Tệp không phải là JSON hợp lệ. Vui lòng chọn tệp đánh giá mô hình đúng định dạng.",
    };
  }

  if (!isPlainObject(parsed)) {
    return {
      ok: false,
      error: "Cấu trúc đánh giá mô hình không hợp lệ (dữ liệu gốc phải là một đối tượng JSON).",
    };
  }

  const rootKeys = [
    "pass_percent",
    "train",
    "test",
    "train_prevalence",
    "test_metrics",
    "test_by_history",
  ] as const;

  if (!hasExactKeys(parsed, rootKeys)) {
    return {
      ok: false,
      error: "Tệp JSON không khớp với lược đồ Đánh giá mô hình hoặc chứa trường không được phép.",
    };
  }

  if (!isPassPercent(parsed.pass_percent)) {
    return {
      ok: false,
      error: "Ngưỡng pass_percent phải nằm trong khoảng từ 0 đến 100.",
    };
  }

  if (!isBoundedRatio(parsed.train_prevalence)) {
    return {
      ok: false,
      error: "Tỷ lệ nền tập huấn luyện (train_prevalence) phải nằm trong đoạn [0, 1].",
    };
  }

  const trainRes = parseSplitSummary(parsed.train, "huấn luyện (train)");
  if (!trainRes.ok) {
    return trainRes;
  }

  const testRes = parseSplitSummary(parsed.test, "kiểm tra (test)");
  if (!testRes.ok) {
    return testRes;
  }

  if (
    !isPlainObject(parsed.test_metrics) ||
    !hasExactKeys(parsed.test_metrics, ["logistic", "prevalence"])
  ) {
    return {
      ok: false,
      error: "Trường test_metrics phải gồm đúng hai mục logistic và prevalence.",
    };
  }

  const logisticRes = parseBinaryMetrics(
    parsed.test_metrics.logistic,
    "hồi quy logistic",
  );
  if (!logisticRes.ok) {
    return logisticRes;
  }

  const prevalenceRes = parseBinaryMetrics(
    parsed.test_metrics.prevalence,
    "mức phổ biến chuẩn (prevalence)",
  );
  if (!prevalenceRes.ok) {
    return prevalenceRes;
  }

  if (
    !isPlainObject(parsed.test_by_history) ||
    !hasExactKeys(parsed.test_by_history, ["with_history", "no_history"])
  ) {
    return {
      ok: false,
      error: "Trường test_by_history phải gồm đúng hai nhóm with_history và no_history.",
    };
  }

  const withHistoryRes = parseHistoryBucket(
    parsed.test_by_history.with_history,
    "có lịch sử hoạt động",
  );
  if (!withHistoryRes.ok) {
    return withHistoryRes;
  }

  const noHistoryRes = parseHistoryBucket(
    parsed.test_by_history.no_history,
    "không có lịch sử hoạt động",
  );
  if (!noHistoryRes.ok) {
    return noHistoryRes;
  }

  return {
    ok: true,
    data: {
      pass_percent: parsed.pass_percent,
      train: trainRes.data,
      test: testRes.data,
      train_prevalence: parsed.train_prevalence,
      test_metrics: {
        logistic: logisticRes.data,
        prevalence: prevalenceRes.data,
      },
      test_by_history: {
        with_history: withHistoryRes.data,
        no_history: noHistoryRes.data,
      },
    },
  };
}

const intFormatter = new Intl.NumberFormat("vi-VN", {
  maximumFractionDigits: 0,
});

const percentFormatter = new Intl.NumberFormat("vi-VN", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

const metricFormatter = new Intl.NumberFormat("vi-VN", {
  minimumFractionDigits: 4,
  maximumFractionDigits: 4,
});

export function formatCount(value: number | null): string {
  if (value === null) {
    return "Không có dữ liệu (null)";
  }
  return intFormatter.format(value);
}

export function formatRatioAsPercent(value: number | null): string {
  if (value === null) {
    return "Không có dữ liệu (null)";
  }
  return `${percentFormatter.format(value * 100)}%`;
}

export function formatPassPercentSetting(value: number): string {
  return `${percentFormatter.format(value)}%`;
}

export function formatMetricValue(value: number | null): string {
  if (value === null) {
    return "Không có dữ liệu (null)";
  }
  return metricFormatter.format(value);
}

export function formatUtcTimestamp(unixSeconds: number): string {
  const date = new Date(unixSeconds * 1000);
  const iso = date.toISOString();
  return `${iso.slice(0, 10)} ${iso.slice(11, 19)} UTC`;
}

export function translateModuleKind(moduleKind: ModuleKind): string {
  return moduleKind === "quiz" ? "Trắc nghiệm (quiz)" : "Bài tập (assign)";
}

export function translatePassStatus(status: PassStatus): string {
  switch (status) {
    case "available":
      return "Đủ dữ liệu xét đạt";
    case "needs_update":
      return "Cần cập nhật điểm";
    case "not_numeric":
      return "Thang điểm phi số";
    case "no_grades":
      return "Chưa có điểm";
  }
}

const REASON_LABELS: Record<string, string> = {
  missing_export: "Thiếu bản xuất lượt quiz",
  no_first_attempt: "Không có lượt làm số 1 trong bản xuất",
  unfinished: "Lượt đầu chưa hoàn tất",
  ungraded: "Lượt đầu chưa được chấm",
  unscored: "Lượt đầu không có thang điểm",
};

export function translateUnlabeledReason(reasonKey: string): string {
  return Object.hasOwn(REASON_LABELS, reasonKey) ? REASON_LABELS[reasonKey] : "Lý do khác";
}
