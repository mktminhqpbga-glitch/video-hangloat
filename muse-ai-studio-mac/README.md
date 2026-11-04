# Muse AI Studio — bản viết lại chạy trên MacBook

Bản này được viết lại từ đầu bằng Python thuần (chỉ dùng thư viện chuẩn),
dựa trên phân tích hành vi bên ngoài của bản Windows `MuseAI-Studio.exe`
1.0.0. Nó **không** phải bản dịch ngược file .exe, không copy mã nguồn
hay khóa cấp phép của bản gốc, và không nhúng bất kỳ khóa, token hay
địa chỉ Google Sheet nào của bản gốc.

## Chạy trên MacBook

Yêu cầu: macOS + Python 3.11 trở lên (macOS mới đã có sẵn `python3`).
Ghép video cần thêm ffmpeg:

```bash
brew install ffmpeg
```

Chạy app:

```bash
python3 app.py
```

App sẽ tự mở `http://127.0.0.1:8765` trên trình duyệt. Dữ liệu lưu ở
`~/.muse-ai-studio-mac/` (cài đặt, tài khoản, dự án, task). Mật khẩu chỉ
lưu dạng hash, không lưu plaintext.

## Tính năng (theo đúng luồng bản gốc)

- **Bản quyền**: Đăng ký mới bằng số điện thoại + mật khẩu để nhận dùng
  thử (mặc định 7 ngày, đổi được trong Cài đặt); Đăng nhập để mở khóa
  tính năng; Nâng cấp Pro bằng mã dạng `AIP-PRO-...`.
- **Tài khoản**: quản lý các tài khoản cấp phép local.
- **Dự án**: tạo dự án Video Affiliate / Phim lồng tiếng / Image-to-Video.
- **Thư viện**: liệt kê file trong thư mục output.
- **Tạo video Affiliate / Làm phim / Image-to-Video**: tạo task vào hàng
  đợi, lưu prompt/kịch bản. Backend tạo video thật cần cấu hình API key
  trong Cài đặt (key chỉ lưu local); khi chưa cấu hình, task chỉ được
  lưu lại và báo rõ là chưa chạy thật — không tạo video giả.
- **Ghép video**: dùng ffmpeg của máy để nối nhiều file video
  (tương đương phần Merge của bản gốc, nhưng dùng ffmpeg hệ thống thay
  vì file ffmpeg Windows 84 MB đi kèm bản gốc).
- **Cài đặt**: số ngày dùng thử, endpoint Google Sheet (tùy chọn),
  API key, đường dẫn ffmpeg, thư mục output.

## Khác biệt cần biết so với bản Windows gốc

- Bản gốc là app Python đóng gói bằng PyInstaller, giao diện web local,
  backend nằm trong các module biên dịch sẵn (`.pyd`) và đi kèm ffmpeg
  Windows — những phần đó không chạy được trên Mac và không được copy
  sang đây. Bản Mac này viết lại phần lõi theo hành vi quan sát được.
- **Cấp phép local**: bản Mac không nối tới Google Sheet cấp phép của
  bản gốc, trừ khi bạn tự nhập endpoint trong Cài đặt. Mã Pro ở đây chỉ
  được kiểm tra định dạng và lưu local. Muốn đồng bộ cấp phép với hệ
  thống Sheet gốc thì cần cấu hình endpoint riêng.
- **AI tạo video/ảnh**: bản gốc gọi các backend AI riêng của nó. Bản Mac
  để trống phần này sau ô cấu hình API key — bạn tự gắn backend mình
  dùng, không có backend nào bị giả lập.
- File `MuseAI-Studio.exe` gốc (Windows, ~120 MB) vẫn là bản phát hành
  chính thức cho Windows; bản Mac này là bản viết lại độc lập, không
  thay thế file exe đó.
