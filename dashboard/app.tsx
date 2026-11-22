import React, { useId, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  type CourseReport,
  formatCount,
  formatMetricValue,
  formatPassPercentSetting,
  formatRatioAsPercent,
  formatUtcTimestamp,
  type ModelEvaluationReport,
  parseCourseReportJson,
  parseModelEvaluationJson,
  translateModuleKind,
  translatePassStatus,
  translateUnlabeledReason,
} from "./report.ts";

type ActiveTab = "course" | "model";

interface ReportSlotState<T> {
  status: "empty" | "loaded" | "error";
  fileName: string | null;
  data: T | null;
  errorMessage: string | null;
}

const initialCourseState: ReportSlotState<CourseReport> = {
  status: "empty",
  fileName: null,
  data: null,
  errorMessage: null,
};

const initialModelState: ReportSlotState<ModelEvaluationReport> = {
  status: "empty",
  fileName: null,
  data: null,
  errorMessage: null,
};

function renderNullOrText(value: number | null, formatted: string) {
  if (value === null) {
    return <span className="null-badge">Không có dữ liệu (null)</span>;
  }
  return <span className="num-value">{formatted}</span>;
}

function CourseReportView({
  state,
  onSelectFile,
  onClear,
}: {
  state: ReportSlotState<CourseReport>;
  onSelectFile: (file: File) => void;
  onClear: () => void;
}) {
  const fileInputId = useId();
  const inputRef = useRef<HTMLInputElement | null>(null);

  const handleFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const chosen = event.target.files?.[0];
    if (!chosen) {
      return;
    }
    onSelectFile(chosen);
  };

  const handleResetClick = () => {
    if (inputRef.current) {
      inputRef.current.value = "";
    }
    onClear();
  };

  const report = state.data;

  return (
    <section
      id="panel-course"
      role="tabpanel"
      aria-labelledby="tab-course"
      className="ledger-section"
    >
      <div className="file-toolbar">
        <div className="file-toolbar-Main">
          <label htmlFor={fileInputId} className="file-label">
            Tệp JSON báo cáo khóa học
          </label>
          <p className="file-helper">
            Đọc trực tiếp trên trình duyệt từ tệp xuất cục bộ. Không tải dữ liệu lên máy chủ.
          </p>
        </div>
        <div className="file-controls">
          <input
            ref={inputRef}
            id={fileInputId}
            type="file"
            accept=".json,application/json"
            onChange={handleFileChange}
            className="file-input"
          />
          {state.status !== "empty" && (
            <button
              type="button"
              onClick={handleResetClick}
              className="secondary-button"
            >
              Xóa tệp đã chọn
            </button>
          )}
        </div>
      </div>

      {state.status === "empty" && (
        <div className="state-box state-empty" role="status">
          <h2>Chưa nạp báo cáo khóa học</h2>
          <p>
            Hãy chọn một tệp JSON báo cáo khóa học để kiểm tra quy mô đoàn hệ, cửa sổ nhật ký
            hoạt động (quiz/assign) và bảng tổng hợp Sổ điểm (Gradebook).
          </p>
        </div>
      )}

      {state.status === "error" && (
        <div className="state-box state-error" role="alert">
          <h2>Không thể đọc báo cáo khóa học</h2>
          <p>
            {state.fileName ? `Tệp "${state.fileName}": ` : ""}
            {state.errorMessage}
          </p>
          <p className="state-recovery-hint">
            Dữ liệu cũ đã được xóa để tránh nhầm lẫn. Bạn có thể chọn lại một tệp JSON hợp lệ ở khung phía trên.
          </p>
        </div>
      )}

      {state.status === "loaded" && report !== null && (
        <div className="report-body">
          <aside className="policy-notice" aria-label="Lưu ý diễn giải nhật ký">
            <strong>Lưu ý diễn giải nhật ký chuẩn:</strong> Số liệu hoạt động bên dưới chỉ phản ánh
            các sự kiện còn lưu trong cửa sổ nhật ký chuẩn của Moodle. Việc không tìm thấy sự kiện trong
            nhật ký còn lưu <em>không phải là bằng chứng</em> học viên không nộp bài. Giá trị{" "}
            <code>0</code> biểu thị đã quan sát được đúng 0 trường hợp, khác hoàn toàn với{" "}
            <code>Không có dữ liệu (null)</code> khi nguồn nhật ký hoặc chỉ số không khả dụng.
          </aside>

          <dl className="metadata-strip" aria-label="Siêu dữ liệu báo cáo khóa học">
            <div className="meta-item">
              <dt>Mã khóa học (Course ID)</dt>
              <dd className="tabular">{formatCount(report.course_id)}</dd>
            </div>
            <div className="meta-item">
              <dt>Quy mô đoàn hệ</dt>
              <dd className="tabular">{formatCount(report.cohort_size)} học viên</dd>
            </div>
            <div className="meta-item">
              <dt>Ngưỡng điểm đạt</dt>
              <dd className="tabular">{formatPassPercentSetting(report.pass_percent)}</dd>
            </div>
            <div className="meta-item">
              <dt>Thời điểm trích xuất</dt>
              <dd className="tabular">{formatUtcTimestamp(report.observed_at)}</dd>
            </div>
            <div className="meta-item">
              <dt>Cửa sổ nhật ký chuẩn</dt>
              <dd className="tabular">
                {formatUtcTimestamp(report.log_window.from)} →{" "}
                {formatUtcTimestamp(report.log_window.to)}
              </dd>
            </div>
            <div className="meta-item">
              <dt>Trạng thái nhật ký chuẩn</dt>
              <dd>
                <span
                  className={
                    report.log_window.standard_log_available
                      ? "status-pill status-ok"
                      : "status-pill status-warn"
                  }
                >
                  {report.log_window.standard_log_available
                    ? "Khả dụng (standard_log_available: true)"
                    : "Không khả dụng (standard_log_available: false)"}
                </span>
              </dd>
            </div>
            <div className="meta-item meta-wide">
              <dt>Mã bản chụp (Snapshot SHA-256)</dt>
              <dd className="mono-hash">{report.snapshot_id}</dd>
            </div>
            {state.fileName && (
              <div className="meta-item">
                <dt>Tệp nguồn</dt>
                <dd>{state.fileName}</dd>
              </div>
            )}
          </dl>

          <section className="ledger-block" aria-labelledby="heading-activities">
            <div className="block-header">
              <h2 id="heading-activities">Hoạt động nộp bài theo nhật ký còn lưu</h2>
              <span className="record-count tabular">
                {formatCount(report.activities.length)} hoạt động
              </span>
            </div>
            <div className="table-scroll" tabIndex={0} role="region" aria-labelledby="heading-activities">
              <table className="ledger-table">
                <caption>
                  Thống kê người nộp bài quan sát được trong cửa sổ nhật ký theo từng mô-đun học tập
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Mã mô-đun (CMID)</th>
                    <th scope="col">Loại hoạt động</th>
                    <th scope="col">Tên hoạt động</th>
                    <th scope="col" className="col-num">
                      Số người nộp quan sát được
                    </th>
                    <th scope="col" className="col-num">
                      Tỷ lệ nộp quan sát được
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {report.activities.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="empty-row">
                        Không có hoạt động trắc nghiệm hoặc bài tập nào trong báo cáo này.
                      </td>
                    </tr>
                  ) : (
                    report.activities.map((act) => (
                      <tr key={act.course_module_id}>
                        <td className="tabular">{formatCount(act.course_module_id)}</td>
                        <td>{translateModuleKind(act.module)}</td>
                        <th scope="row" className="row-title">
                          {act.name}
                        </th>
                        <td className="col-num tabular">
                          {renderNullOrText(
                            act.observed_submitters,
                            formatCount(act.observed_submitters),
                          )}
                        </td>
                        <td className="col-num tabular">
                          {renderNullOrText(
                            act.observed_submission_share,
                            formatRatioAsPercent(act.observed_submission_share),
                          )}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section className="ledger-block" aria-labelledby="heading-gradebook">
            <div className="block-header">
              <h2 id="heading-gradebook">Bảng tổng hợp Sổ điểm (Gradebook)</h2>
              <span className="record-count tabular">
                {formatCount(report.grade_items.length)} hạng mục điểm
              </span>
            </div>
            <div className="table-scroll" tabIndex={0} role="region" aria-labelledby="heading-gradebook">
              <table className="ledger-table">
                <caption>
                  Tình trạng chấm điểm và tỷ lệ đạt trên từng hạng mục trong Sổ điểm
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Mã hạng mục</th>
                    <th scope="col">Mã mô-đun (CMID)</th>
                    <th scope="col">Tên hạng mục điểm</th>
                    <th scope="col">Hiển thị</th>
                    <th scope="col">Trạng thái xét đạt</th>
                    <th scope="col" className="col-num">
                      Số học viên đã chấm
                    </th>
                    <th scope="col" className="col-num">
                      Số học viên đạt
                    </th>
                    <th scope="col" className="col-num">
                      Tỷ lệ đạt
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {report.grade_items.length === 0 ? (
                    <tr>
                      <td colSpan={8} className="empty-row">
                        Không có hạng mục sổ điểm nào trong báo cáo này.
                      </td>
                    </tr>
                  ) : (
                    report.grade_items.map((item) => (
                      <tr key={item.grade_item_id}>
                        <td className="tabular">{formatCount(item.grade_item_id)}</td>
                        <td className="tabular">{formatCount(item.course_module_id)}</td>
                        <th scope="row" className="row-title">
                          {item.name}
                        </th>
                        <td>
                          <span
                            className={
                              item.hidden
                                ? "status-pill status-muted"
                                : "status-pill status-ok"
                            }
                          >
                            {item.hidden ? "Đang ẩn" : "Hiển thị"}
                          </span>
                        </td>
                        <td>{translatePassStatus(item.pass_status)}</td>
                        <td className="col-num tabular">
                          {formatCount(item.graded_learners)}
                        </td>
                        <td className="col-num tabular">
                          {renderNullOrText(
                            item.passed_learners,
                            formatCount(item.passed_learners),
                          )}
                        </td>
                        <td className="col-num tabular">
                          {renderNullOrText(
                            item.pass_rate,
                            formatRatioAsPercent(item.pass_rate),
                          )}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      )}
    </section>
  );
}

function ModelEvaluationView({
  state,
  onSelectFile,
  onClear,
}: {
  state: ReportSlotState<ModelEvaluationReport>;
  onSelectFile: (file: File) => void;
  onClear: () => void;
}) {
  const fileInputId = useId();
  const inputRef = useRef<HTMLInputElement | null>(null);

  const handleFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const chosen = event.target.files?.[0];
    if (!chosen) {
      return;
    }
    onSelectFile(chosen);
  };

  const handleResetClick = () => {
    if (inputRef.current) {
      inputRef.current.value = "";
    }
    onClear();
  };

  const report = state.data;

  const allReasons = React.useMemo(() => {
    if (!report) {
      return [];
    }
    const keys = new Set<string>([
      ...Object.keys(report.train.unlabeled),
      ...Object.keys(report.test.unlabeled),
    ]);
    return Array.from(keys).sort();
  }, [report]);

  return (
    <section
      id="panel-model"
      role="tabpanel"
      aria-labelledby="tab-model"
      className="ledger-section"
    >
      <div className="file-toolbar">
        <div className="file-toolbar-Main">
          <label htmlFor={fileInputId} className="file-label">
            Tệp JSON đánh giá mô hình
          </label>
          <p className="file-helper">
            Kiểm tra độ bao phủ nhãn, sai số Brier và độ chính xác của mô hình thử nghiệm cục bộ.
          </p>
        </div>
        <div className="file-controls">
          <input
            ref={inputRef}
            id={fileInputId}
            type="file"
            accept=".json,application/json"
            onChange={handleFileChange}
            className="file-input"
          />
          {state.status !== "empty" && (
            <button
              type="button"
              onClick={handleResetClick}
              className="secondary-button"
            >
              Xóa tệp đã chọn
            </button>
          )}
        </div>
      </div>

      {state.status === "empty" && (
        <div className="state-box state-empty" role="status">
          <h2>Chưa nạp báo cáo đánh giá mô hình</h2>
          <p>
            Hãy chọn một tệp JSON đánh giá mô hình để xem phân bổ mẫu huấn luyện/kiểm tra, lý do
            thiếu nhãn, cũng như so sánh hồi quy logistic với đường cơ sở theo tỷ lệ phổ biến.
          </p>
        </div>
      )}

      {state.status === "error" && (
        <div className="state-box state-error" role="alert">
          <h2>Không thể đọc báo cáo đánh giá mô hình</h2>
          <p>
            {state.fileName ? `Tệp "${state.fileName}": ` : ""}
            {state.errorMessage}
          </p>
          <p className="state-recovery-hint">
            Dữ liệu cũ đã được xóa. Vui lòng chọn lại tệp JSON đánh giá mô hình hợp lệ.
          </p>
        </div>
      )}

      {state.status === "loaded" && report !== null && (
        <div className="report-body">
          <aside className="policy-notice policy-caveat" aria-label="Cảnh báo mô hình thử nghiệm">
            <strong>Cảnh báo giới hạn sử dụng (Chỉ mang tính thử nghiệm):</strong> Kết quả đánh giá
            mô hình dưới đây chỉ phục vụ mục đích nghiên cứu kỹ thuật cục bộ trên dữ liệu lịch sử.
            Tuyệt đối không dùng các chỉ số này để ra quyết định tự động, xếp loại hay can thiệp bất lợi
            đối với từng học viên.
          </aside>

          <dl className="metadata-strip" aria-label="Thông số chung của mô hình">
            <div className="meta-item">
              <dt>Ngưỡng điểm đạt (pass_percent)</dt>
              <dd className="tabular">{formatPassPercentSetting(report.pass_percent)}</dd>
            </div>
            <div className="meta-item">
              <dt>Tỷ lệ đạt tập huấn luyện (train_prevalence)</dt>
              <dd className="tabular">{formatRatioAsPercent(report.train_prevalence)}</dd>
            </div>
            <div className="meta-item">
              <dt>Mẫu có nhãn tập huấn luyện</dt>
              <dd className="tabular">
                {formatCount(report.train.labeled)} / {formatCount(report.train.cohort_size)}
              </dd>
            </div>
            <div className="meta-item">
              <dt>Mẫu có nhãn tập kiểm tra</dt>
              <dd className="tabular">
                {formatCount(report.test.labeled)} / {formatCount(report.test.cohort_size)}
              </dd>
            </div>
            {state.fileName && (
              <div className="meta-item">
                <dt>Tệp nguồn</dt>
                <dd>{state.fileName}</dd>
              </div>
            )}
          </dl>

          <section className="ledger-block" aria-labelledby="heading-coverage">
            <div className="block-header">
              <h2 id="heading-coverage">Độ bao phủ nhãn giữa tập Huấn luyện và Kiểm tra</h2>
            </div>
            <div className="table-scroll" tabIndex={0} role="region" aria-labelledby="heading-coverage">
              <table className="ledger-table">
                <caption>
                  Thống kê quy mô đoàn hệ, số mẫu gán nhãn được và số mẫu đạt ở từng phân tập
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Phân tập dữ liệu</th>
                    <th scope="col" className="col-num">
                      Quy mô đoàn hệ
                    </th>
                    <th scope="col" className="col-num">
                      Số mẫu có nhãn
                    </th>
                    <th scope="col" className="col-num">
                      Tỷ lệ bao phủ nhãn
                    </th>
                    <th scope="col" className="col-num">
                      Số mẫu đạt (passed)
                    </th>
                    <th scope="col" className="col-num">
                      Số mẫu thiếu nhãn
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {[
                    { key: "train", label: "Tập huấn luyện (train)", split: report.train },
                    { key: "test", label: "Tập kiểm tra (test)", split: report.test },
                  ].map(({ key, label, split }) => {
                    const coverage =
                      split.cohort_size > 0 ? split.labeled / split.cohort_size : 0;
                    const unlabeledCount = split.cohort_size - split.labeled;
                    return (
                      <tr key={key}>
                        <th scope="row" className="row-title">
                          {label}
                        </th>
                        <td className="col-num tabular">{formatCount(split.cohort_size)}</td>
                        <td className="col-num tabular">{formatCount(split.labeled)}</td>
                        <td className="col-num tabular">{formatRatioAsPercent(coverage)}</td>
                        <td className="col-num tabular">{formatCount(split.passed)}</td>
                        <td className="col-num tabular">{formatCount(unlabeledCount)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>

          <section className="ledger-block" aria-labelledby="heading-unlabeled">
            <div className="block-header">
              <h2 id="heading-unlabeled">Phân tích lý do thiếu nhãn (unlabeled)</h2>
            </div>
            <div className="table-scroll" tabIndex={0} role="region" aria-labelledby="heading-unlabeled">
              <table className="ledger-table">
                <caption>
                  Số lượng bản ghi không thể gán nhãn đạt/chưa đạt phân theo nguyên nhân
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Nguyên nhân thiếu nhãn</th>
                    <th scope="col" className="col-num">
                      Tập huấn luyện (train)
                    </th>
                    <th scope="col" className="col-num">
                      Tập kiểm tra (test)
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {allReasons.length === 0 ? (
                    <tr>
                      <td colSpan={3} className="empty-row">
                        Không có mẫu nào bị thiếu nhãn trong cả hai tập dữ liệu (0 mẫu thiếu nhãn).
                      </td>
                    </tr>
                  ) : (
                    allReasons.map((reason) => (
                      <tr key={reason}>
                        <th scope="row" className="row-title">
                          {translateUnlabeledReason(reason)}
                        </th>
                        <td className="col-num tabular">
                          {formatCount(report.train.unlabeled[reason] ?? 0)}
                        </td>
                        <td className="col-num tabular">
                          {formatCount(report.test.unlabeled[reason] ?? 0)}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section className="ledger-block" aria-labelledby="heading-metrics">
            <div className="block-header">
              <h2 id="heading-metrics">
                So sánh hiệu năng trên tập kiểm tra: Hồi quy Logistic và Đường cơ sở (Prevalence)
              </h2>
            </div>
            <div className="table-scroll" tabIndex={0} role="region" aria-labelledby="heading-metrics">
              <table className="ledger-table">
                <caption>
                  Điểm Brier (càng thấp càng tốt) và Độ chính xác tại ngưỡng 0,5 (càng cao càng tốt)
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Phương pháp dự báo</th>
                    <th scope="col" className="col-num">
                      Điểm Brier (Brier score)
                    </th>
                    <th scope="col" className="col-num">
                      Độ chính xác ở ngưỡng 0,5
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <th scope="row" className="row-title">
                      Mô hình hồi quy Logistic (logistic)
                    </th>
                    <td className="col-num tabular">
                      {formatMetricValue(report.test_metrics.logistic.brier)}
                    </td>
                    <td className="col-num tabular">
                      {formatRatioAsPercent(report.test_metrics.logistic.accuracy_at_half)}
                    </td>
                  </tr>
                  <tr>
                    <th scope="row" className="row-title">
                      Mốc so sánh theo tỷ lệ đạt tập huấn luyện
                    </th>
                    <td className="col-num tabular">
                      {formatMetricValue(report.test_metrics.prevalence.brier)}
                    </td>
                    <td className="col-num tabular">
                      {formatRatioAsPercent(report.test_metrics.prevalence.accuracy_at_half)}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <section className="ledger-block" aria-labelledby="heading-history">
            <div className="block-header">
              <h2 id="heading-history">Kết quả kiểm tra theo lịch sử điểm</h2>
            </div>
            <div className="table-scroll" tabIndex={0} role="region" aria-labelledby="heading-history">
              <table className="ledger-table">
                <caption>
                  So sánh nhóm có và không có điểm quiz tiên quyết trước mốc dự đoán
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Nhóm học viên trong tập kiểm tra</th>
                    <th scope="col" className="col-num">
                      Số mẫu có nhãn (labeled)
                    </th>
                    <th scope="col" className="col-num">
                      Điểm Brier (Brier score)
                    </th>
                    <th scope="col" className="col-num">
                      Độ chính xác ở ngưỡng 0,5
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {[
                    {
                      key: "with_history",
                      label: "Có điểm quiz tiên quyết",
                      bucket: report.test_by_history.with_history,
                    },
                    {
                      key: "no_history",
                      label: "Không có điểm quiz tiên quyết",
                      bucket: report.test_by_history.no_history,
                    },
                  ].map(({ key, label, bucket }) => (
                    <tr key={key}>
                      <th scope="row" className="row-title">
                        {label}
                      </th>
                      <td className="col-num tabular">{formatCount(bucket.labeled)}</td>
                      <td className="col-num tabular">
                        {renderNullOrText(bucket.brier, formatMetricValue(bucket.brier))}
                      </td>
                      <td className="col-num tabular">
                        {renderNullOrText(
                          bucket.accuracy_at_half,
                          formatRatioAsPercent(bucket.accuracy_at_half),
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      )}
    </section>
  );
}

function DashboardApp() {
  const [activeTab, setActiveTab] = useState<ActiveTab>("course");
  const [courseState, setCourseState] =
    useState<ReportSlotState<CourseReport>>(initialCourseState);
  const [modelState, setModelState] =
    useState<ReportSlotState<ModelEvaluationReport>>(initialModelState);

  const courseLoadTokenRef = useRef(0);
  const modelLoadTokenRef = useRef(0);

  const handleCourseFile = async (file: File) => {
    const currentToken = ++courseLoadTokenRef.current;
    try {
      const rawText = await file.text();
      if (currentToken !== courseLoadTokenRef.current) {
        return;
      }
      const parsed = parseCourseReportJson(rawText);
      if (!parsed.ok) {
        setCourseState({
          status: "error",
          fileName: file.name,
          data: null,
          errorMessage: parsed.error,
        });
        return;
      }
      setCourseState({
        status: "loaded",
        fileName: file.name,
        data: parsed.data,
        errorMessage: null,
      });
    } catch {
      if (currentToken !== courseLoadTokenRef.current) {
        return;
      }
      setCourseState({
        status: "error",
        fileName: file.name,
        data: null,
        errorMessage: "Không thể đọc nội dung tệp từ đĩa cục bộ. Vui lòng thử lại.",
      });
    }
  };

  const handleClearCourse = () => {
    courseLoadTokenRef.current += 1;
    setCourseState(initialCourseState);
  };

  const handleModelFile = async (file: File) => {
    const currentToken = ++modelLoadTokenRef.current;
    try {
      const rawText = await file.text();
      if (currentToken !== modelLoadTokenRef.current) {
        return;
      }
      const parsed = parseModelEvaluationJson(rawText);
      if (!parsed.ok) {
        setModelState({
          status: "error",
          fileName: file.name,
          data: null,
          errorMessage: parsed.error,
        });
        return;
      }
      setModelState({
        status: "loaded",
        fileName: file.name,
        data: parsed.data,
        errorMessage: null,
      });
    } catch {
      if (currentToken !== modelLoadTokenRef.current) {
        return;
      }
      setModelState({
        status: "error",
        fileName: file.name,
        data: null,
        errorMessage: "Không thể đọc nội dung tệp từ đĩa cục bộ. Vui lòng thử lại.",
      });
    }
  };

  const handleClearModel = () => {
    modelLoadTokenRef.current += 1;
    setModelState(initialModelState);
  };

  const handleTabKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      event.preventDefault();
      setActiveTab((prev) => (prev === "course" ? "model" : "course"));
      const next = activeTab === "course" ? "model" : "course";
      event.currentTarget.querySelector<HTMLButtonElement>(`#tab-${next}`)?.focus();
    }
  };

  return (
    <div className="ledger-shell">
      <header className="ledger-header">
        <div className="header-titles">
          <p className="ledger-kicker">Sổ cái phân tích học tập nội bộ · Chế độ ngoại tuyến</p>
          <h1>Bảng kiểm định dữ liệu Moodle</h1>
        </div>
        <nav aria-label="Chế độ xem báo cáo">
          <div
            role="tablist"
            aria-label="Chọn loại báo cáo"
            className="view-tablist"
            onKeyDown={handleTabKeyDown}
          >
            <button
              id="tab-course"
              type="button"
              role="tab"
              aria-selected={activeTab === "course"}
              aria-controls="panel-course"
              tabIndex={activeTab === "course" ? 0 : -1}
              onClick={() => setActiveTab("course")}
              className={
                activeTab === "course" ? "tab-button tab-active" : "tab-button"
              }
            >
              <span>Báo cáo khóa học</span>
              <span className="tab-indicator">
                {courseState.status === "loaded"
                  ? "Đã nạp"
                  : courseState.status === "error"
                    ? "Lỗi tệp"
                    : "Trống"}
              </span>
            </button>
            <button
              id="tab-model"
              type="button"
              role="tab"
              aria-selected={activeTab === "model"}
              aria-controls="panel-model"
              tabIndex={activeTab === "model" ? 0 : -1}
              onClick={() => setActiveTab("model")}
              className={
                activeTab === "model" ? "tab-button tab-active" : "tab-button"
              }
            >
              <span>Đánh giá mô hình</span>
              <span className="tab-indicator">
                {modelState.status === "loaded"
                  ? "Đã nạp"
                  : modelState.status === "error"
                    ? "Lỗi tệp"
                    : "Trống"}
              </span>
            </button>
          </div>
        </nav>
      </header>

      <main className="ledger-main">
        {activeTab === "course" ? (
          <CourseReportView
            state={courseState}
            onSelectFile={handleCourseFile}
            onClear={handleClearCourse}
          />
        ) : (
          <ModelEvaluationView
            state={modelState}
            onSelectFile={handleModelFile}
            onClear={handleClearModel}
          />
        )}
      </main>

      <footer className="ledger-footer">
        <p>
          Xử lý hoàn toàn trong bộ nhớ trình duyệt bằng <code>File.text()</code>. Không lưu trữ,
          không gửi yêu cầu mạng và không hiển thị danh tính học viên.
        </p>
      </footer>
    </div>
  );
}

const container = document.getElementById("root");
if (container) {
  const root = createRoot(container);
  root.render(<DashboardApp />);
}
