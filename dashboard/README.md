# Moodle Analytics dashboard

Giao diện React chạy bằng Bun trên máy của bạn. Xem báo cáo khóa học và kết quả đánh giá dự đoán từ JSON tổng hợp; không tải dữ liệu lên máy chủ.

## Chạy giao diện

Cần Bun 1.4. Từ thư mục gốc repository:

```bash
cd dashboard
bun install --frozen-lockfile --ignore-scripts
bun run start
```

Mở <http://127.0.0.1:4173>. Bun chỉ lắng nghe trên `127.0.0.1`; không công khai ra LAN. Lần cài dependencies cần registry hoặc cache cục bộ; khi chạy dashboard không có CDN hay dịch vụ bên ngoài. Có thể đổi cổng bằng `PORT=4180 bun run start`.

## Tạo dữ liệu để mở

Từ thư mục gốc, trong môi trường Python đã cài `analytics/requirements.txt`, dùng file DuckDB đã nhập. Thay các đường dẫn và `SNAPSHOT_ID` bằng giá trị thực tế:

```bash
umask 077
python3 -m analytics.report_snapshot /path/to/private/course.duckdb \
  --snapshot-id "$SNAPSHOT_ID" --pass-percent 60 > /path/to/private/course-report.json
python3 -m analytics.predict_baseline /path/to/private/course.duckdb \
  /path/to/private/experiment.json > /path/to/private/evaluation.json
```

Chọn **Báo cáo khóa học** hoặc **Đánh giá mô hình** và mở tệp tương ứng. Không chọn bản xuất Moodle thô hay file DuckDB. Tệp được đọc trong bộ nhớ trình duyệt, không upload, không lưu trong browser storage, và bị mất khi tải lại trang. Để đổi mốc, ngưỡng hoặc chạy lại mô hình, tạo JSON mới bằng Python rồi mở lại tệp.

Báo cáo không coi thiếu log là chưa nộp. Tỷ lệ đạt dựa trên điểm Gradebook và ngưỡng đã chọn khi tạo JSON. Chỉ số mô hình là đánh giá thực nghiệm, không dùng để tự động ra quyết định cho học viên. Ngay cả JSON tổng hợp cũng có thể chứa tên hoạt động và số liệu của nhóm nhỏ; lưu trong thư mục riêng.
