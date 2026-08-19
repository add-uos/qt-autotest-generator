// ==================== 常用 Stub 模式 ====================

// 1. UI 显示/隐藏（QWidget, QDialog）
stub.set_lamda(&QWidget::show, [](QWidget *) {
    __DBG_STUB_INVOKE__
});

stub.set_lamda(&QWidget::hide, [](QWidget *) {
    __DBG_STUB_INVOKE__
});

// 对话框执行
stub.set_lamda(VADDR(QDialog, exec), [] {
    __DBG_STUB_INVOKE__
    return QDialog::Accepted;  // 或 QDialog::Rejected
});

// 2. QWidget 尺寸和位置
stub.set_lamda(&QWidget::height, [](QWidget *) -> int {
    __DBG_STUB_INVOKE__
    return 600;  // Mock 高度
});

stub.set_lamda(&QWidget::width, [](QWidget *) -> int {
    __DBG_STUB_INVOKE__
    return 800;  // Mock 宽度
});

stub.set_lamda(&QWidget::x, [](QWidget *) -> int {
    __DBG_STUB_INVOKE__
    return 100;  // Mock X 坐标
});

stub.set_lamda(&QWidget::y, [](QWidget *) -> int {
    __DBG_STUB_INVOKE__
    return 200;  // Mock Y 坐标
});

// 3. QWidget 边距和内容区域
stub.set_lamda(&QWidget::contentsMargins, [](QWidget *) -> QMargins {
    __DBG_STUB_INVOKE__
    return QMargins(10, 10, 10, 10);
});

stub.set_lamda(&QWidget::contentsRect, [](QWidget *) -> QRect {
    __DBG_STUB_INVOKE__
    return QRect(0, 0, 800, 600);
});

// 4. 信号监听（QSignalSpy）
QSignalSpy spy(obj, &{ClassName}::{SignalName});

// 触发信号后验证
EXPECT_EQ(spy.count(), 1);
EXPECT_EQ(spy.at(0).at(0).toInt(), expected);

// 5. 虚函数（使用 VADDR 宏）
stub.set_lamda(VADDR({ClassName}, {MethodName}), []() {
    __DBG_STUB_INVOKE__
});

// 虚函数有返回值
stub.set_lamda(VADDR({ClassName}, {MethodName}), []() -> int {
    __DBG_STUB_INVOKE__
    return 42;
});

// 虚函数有参数
stub.set_lamda(
    VADDR({ClassName}, {MethodName}),
    []({ClassName} *self, int arg1, QString arg2) -> bool {
        __DBG_STUB_INVOKE__
        EXPECT_EQ(arg1, expected);
        EXPECT_EQ(arg2, expectedStr);
        return true;
    }
);

// 6. 重载函数（使用 static_cast）
stub.set_lamda(
    static_cast<int ({ClassName}::*)(int, int)>(&{ClassName}::{MethodName}),
    []({ClassName} *self, int a, int b) -> int {
        __DBG_STUB_INVOKE__
        return a + b;
    }
);

stub.set_lamda(
    static_cast<QString ({ClassName}::*)(const QString &)>(&{ClassName}::{MethodName}),
    []({ClassName} *self, const QString &str) -> QString {
        __DBG_STUB_INVOKE__
        return "mock: " + str;
    }
);

// 7. 外部依赖
stub.set_lamda(&ExternalClass::method, [](ExternalClass *self, QString param) {
    __DBG_STUB_INVOKE__
    EXPECT_EQ(param, "expected");
    return true;
});

// 外部全局函数
stub.set_lamda(qPrintable, [](const QString &str) -> const char* {
    __DBG_STUB_INVOKE__
    static QString mockResult;
    mockResult = "mock: " + str;
    return mockResult.toLocal8Bit().constData();
});

// 8. 文件操作（QFile）
stub.set_lamda(&QFile::open, [](QFile *self, QIODevice::OpenMode mode) -> bool {
    __DBG_STUB_INVOKE__
    return true;  // Mock 打开成功
});

stub.set_lamda(&QFile::readAll, [](QFile *self) -> QByteArray {
    __DBG_STUB_INVOKE__
    return "mock content";
});

stub.set_lamda(&QFile::write, [](QFile *self, const QByteArray &data) -> qint64 {
    __DBG_STUB_INVOKE__
    return data.size();  // Mock 写入成功
});

stub.set_lamda(&QFile::close, [](QFile *self) -> void {
    __DBG_STUB_INVOKE__
});

// 9. 目录操作（QDir）
stub.set_lamda(&QDir::exists, [](QDir *self) -> bool {
    __DBG_STUB_INVOKE__
    return true;
});

stub.set_lamda(&QDir::entryList, [](QDir *self) -> QStringList {
    __DBG_STUB_INVOKE__
    return {"file1.txt", "file2.txt"};
});

// 10. 事件处理
stub.set_lamda(&QObject::eventFilter, [](QObject *self, QObject *watched, QEvent *event) -> bool {
    __DBG_STUB_INVOKE__
    return false;  // 不拦截事件
});

stub.set_lamda(&QWidget::keyPressEvent, [](QWidget *self, QKeyEvent *event) {
    __DBG_STUB_INVOKE__
    // Mock 键盘事件处理
});

stub.set_lamda(&QWidget::mousePressEvent, [](QWidget *self, QMouseEvent *event) {
    __DBG_STUB_INVOKE__
    // Mock 鼠标事件处理
});

// 11. 网络请求（QNetworkReply）
stub.set_lamda(&QNetworkReply::readAll, [](QNetworkReply *self) -> QByteArray {
    __DBG_STUB_INVOKE__
    return "mock network response";
});

stub.set_lamda(&QNetworkReply::error, [](QNetworkReply *self) -> QNetworkReply::NetworkError {
    __DBG_STUB_INVOKE__
    return QNetworkReply::NoError;
});

// 12. 定时器（QTimer）
stub.set_lamda(&QTimer::start, [](QTimer *self, int msec) {
    __DBG_STUB_INVOKE__
});

stub.set_lamda(&QTimer::stop, [](QTimer *self) {
    __DBG_STUB_INVOKE__
});

// 13. 数据库（QSqlQuery）
stub.set_lamda(&QSqlQuery::exec, [](QSqlQuery *self, const QString &query) -> bool {
    __DBG_STUB_INVOKE__
    return true;
});

stub.set_lamda(&QSqlQuery::next, [](QSqlQuery *self) -> bool {
    __DBG_STUB_INVOKE__
    return false;  // Mock 没有更多数据
});

// 14. 设置（QSettings）
stub.set_lamda(&QSettings::value, [](QSettings *self, const QString &key) -> QVariant {
    __DBG_STUB_INVOKE__
    return "mock value";
});

stub.set_lamda(&QSettings::setValue, [](QSettings *self, const QString &key, const QVariant &value) {
    __DBG_STUB_INVOKE__
});

// 15. 其他常用函数
stub.set_lamda(&QObject::deleteLater, [](QObject *self) {
    __DBG_STUB_INVOKE__
});

stub.set_lamda(&QObject::parent, [](QObject *self) -> QObject* {
    __DBG_STUB_INVOKE__
    return nullptr;
});

stub.set_lamda(&QObject::objectName, [](QObject *self) -> QString {
    __DBG_STUB_INVOKE__
    return "mock object";
});

// 16. Stub 内参数断言 + 调用计数器（验证副作用，避免"不崩溃就过"）
//
// 重要：计数器/顺序容器必须是【测试夹具成员】（非 static/全局），在 SetUp() 中
// reset、TearDown() 中 stub.clear()。禁止用 static/全局变量——会跨用例污染，
// self_checker 5b 会判违规（"用例间污染"）。
//
// 夹具示例（16b/16c/16d 依赖此结构）：
// class MyClassTest : public ::testing::Test {
// protected:
//     int call_count = 0;
//     QStringList call_order;
//     int forbidden_count = 0;
//     stub_ext::StubExt stub;
//     void SetUp() override {
//         call_count = 0;
//         call_order.clear();
//         forbidden_count = 0;
//         // 下方 stub.set_lamda(...) 在此设置
//     }
//     void TearDown() override { stub.clear(); }
// };

// 16a. 参数断言：在 stub lambda 内 EXPECT_EQ 验证传入参数
stub.set_lamda(&ExternalClass::save, [](ExternalClass *self, const QString &path, const QByteArray &data) -> bool {
    __DBG_STUB_INVOKE__
    EXPECT_EQ(path, QString("expected_dir/file.txt"));   // 验证传入路径（用期望字符串，勿硬编码绝对路径，否则 5b 误报）
    EXPECT_EQ(data, QByteArray("expected content"));     // 验证传入内容
    return true;
});

// 16b. 调用计数器：验证方法是否触发下游调用 + 调用次数
// call_count 为夹具成员，SetUp() 已 reset 为 0；lambda 用 [this] 捕获夹具成员
// （此处用 ExternalClass::flush 示范，避免与 #15 的 deleteLater 重复 stub 同一方法；
//   真实使用时按待测方法实际调用的下游选目标，勿对同一方法重复 set_lamda）
stub.set_lamda(&ExternalClass::flush, [this](ExternalClass *self) {
    __DBG_STUB_INVOKE__
    ++call_count;  // 每次 stub 命中自增（夹具成员，每用例自动重置）
});
// Act 后：EXPECT_EQ(call_count, 1);  // 验证 flush 被调用且仅调一次

// 16c. 调用顺序验证（多个 stub 的命中顺序）
// call_order 为夹具成员，SetUp() 已 clear
stub.set_lamda(&ClassA::step1, [this](ClassA *self) {
    __DBG_STUB_INVOKE__
    call_order << "step1";
});
stub.set_lamda(&ClassB::step2, [this](ClassB *self, int x) -> int {
    __DBG_STUB_INVOKE__
    call_order << "step2";
    return x * 2;
});
// Act 后：
// EXPECT_EQ(call_order.size(), 2);
// EXPECT_EQ(call_order.at(0), QString("step1"));
// EXPECT_EQ(call_order.at(1), QString("step2"));

// 16d. 未调用验证：确保某依赖未被触发（正常路径不应走到 error 分支）
// forbidden_count 为夹具成员，SetUp() 已 reset 为 0
stub.set_lamda(&Logger::error, [this](Logger *self, const QString &msg) {
    __DBG_STUB_INVOKE__
    ++forbidden_count;
});
// Act 后：EXPECT_EQ(forbidden_count, 0);  // 正常路径不应触发 error 日志

// 16e. QSignalSpy 参数精确验证（不只 count，要验证参数值）
QSignalSpy spy(obj, &MyClass::dataChanged);
// Act 后：
// EXPECT_EQ(spy.count(), 1);
// EXPECT_EQ(spy.at(0).at(0).toInt(), 42);                // 第1参数精确值
// EXPECT_EQ(spy.at(0).at(1).toString(), QString("ok"));  // 第2参数精确值

// 16f. 对象状态前后对比（验证副作用改变了对象内部状态）
// Arrange
obj->setCount(0);
// Act
obj->addItem("item1");
obj->addItem("item2");
// Assert
EXPECT_EQ(obj->count(), 2);                       // 状态变更
EXPECT_EQ(obj->itemAt(0), QString("item1"));      // 内容验证

// ==================== Stub 模式结束 ====================

// ==================== gMock 模式（用于项目内接口类，详见 test-types.md §7） ====================
//
// 何时用 gMock？依赖是【项目内定义的类】且【有虚函数/纯虚接口】且【可注入】（被测类
// 通过指针/引用持有它，可在构造时传入 mock）。三者全满足才用 gMock；否则用 stub_ext
// （上方第 1-16 节）。决策树见 test-types.md §7.6。
//
// 何时用 stub_ext？依赖是 Qt 内置类、全局函数、第三方类、或项目内类但无虚函数/不可
// 注入。两者【禁止】对同一目标方法混用（test-types §7.5 反模式 A9）。
//
// 使用 gMock 的测试文件顶部必须 #include <gmock/gmock.h>（google-test-base.cpp 默认
// 只 include gtest/gtest.h，gMock include 由 test_writer 按需追加）。

// 17. Mock 类定义（gMock）
//
// ⚠️⚠️⚠️ 以下 #include <gmock/gmock.h> 仅为展示 gMock 模式起始位置！
// 实际使用时，gMock include 必须放测试文件顶部（与 #include <gtest/gtest.h> 同区）。
// 绝不可照搬此位置到文件中部！
//
// 假设被测代码有接口：
//   class IStorage {
//   public:
//       virtual ~IStorage() = default;
//       virtual bool save(const QString &path, const QByteArray &data) = 0;
//       virtual QByteArray load(const QString &path) = 0;
//       virtual void clear() = 0;
//   };
//
// 测试代码：定义 Mock 类（仅测试文件内可见，不动项目源码）
// 注：实际工程中 #include <gmock/gmock.h> 应放测试文件顶部（与 #include <gtest/gtest.h> 同区），
// 此处仅为展示 gMock 模式起始，不可照搬到文件中部
#include <gmock/gmock.h>

class MockStorage : public IStorage {
public:
    MOCK_METHOD(bool, save,
                (const QString &path, const QByteArray &data), (override));
    MOCK_METHOD(QByteArray, load,
                (const QString &path), (override));
    MOCK_METHOD(void, clear, (), (override));
};

// 18. EXPECT_CALL：验证"被调用了、调用了几次、传了什么参数"
TEST_F(ManagerTest, Save_CallsStorageOnce_ReturnsTrue) {
    MockStorage storage;
    Manager mgr(&storage);  // 依赖注入：被测类通过指针持有 IStorage

    // 期望：save 被调用恰好 1 次，参数必须等于 ("cfg.txt", "data")
    EXPECT_CALL(storage, save("cfg.txt", QByteArray("data")))
        .Times(1)
        .WillOnce(::testing::Return(true));

    // Act
    bool ok = mgr.persist("cfg.txt", "data");

    // Assert：行为已由 EXPECT_CALL 验证；这里只断言 SUT 返回
    EXPECT_TRUE(ok);
    // 析构时 gMock 自动校验所有 EXPECT_CALL 是否满足，未满足则用例失败
}

// 18a. 验证【未被调用】：Times(0)
TEST_F(ManagerTest, Save_InvalidInput_SkipsStorageAndReturnsFalse) {
    MockStorage storage;
    Manager mgr(&storage);

    EXPECT_CALL(storage, save(::testing::_, ::testing::_))
        .Times(0);  // 任何形式的 save 都不应被触发

    EXPECT_FALSE(mgr.persist("", ""));  // 空输入直接拒绝，不走 storage
}

// 18b. 验证【调用顺序】：InSequence
TEST_F(ManagerTest, Save_ReloadAndSave_LoadsBeforeSaves) {
    MockStorage storage;
    Manager mgr(&storage);

    ::testing::InSequence seq;
    EXPECT_CALL(storage, load(::testing::_))
        .WillOnce(::testing::Return(QByteArray("")));
    EXPECT_CALL(storage, save(::testing::_, ::testing::_))
        .WillOnce(::testing::Return(true));

    mgr.reloadAndSave();
}

// 19. ON_CALL：配置默认返回值（stub 行为，不强制次数）
//
// 与 EXPECT_CALL 区别：
// - EXPECT_CALL：默认会校验 Times(1)；验证"被调用情况"
// - ON_CALL：仅配置默认返回，不校验次数；适合"喂输入"场景
TEST_F(ManagerTest, Load_DefaultConfig_ReturnsCached) {
    MockStorage storage;
    Manager mgr(&storage);

    ON_CALL(storage, load(::testing::_))
        .WillByDefault(::testing::Return(QByteArray("cached")));

    // 多次调用 load 都返回 "cached"，不会因次数校验失败
    EXPECT_EQ(mgr.read("a"), QString("cached"));
    EXPECT_EQ(mgr.read("b"), QString("cached"));
}

// 19a. Cardinality 速查（完整见 test-types.md §7.4.4）
//   Times(0)        0 次（验证未调用）
//   Times(1)        恰好 1 次（EXPECT_CALL 默认）
//   Times(2)        恰好 2 次
//   AtLeast(n)      ≥ n
//   AtMost(n)       ≤ n
//   Between(m, n)   [m, n]
//   AnyNumber()     任意次（相当于 ON_CALL 的次数语义）

// 19b. Matcher 速查（完整见 test-types.md §7.4.5）
//   _                       任意
//   Eq(v) / 直接写 v       等于
//   StrEq(s)                字符串等于
//   StartsWith(p)           前缀
//   Contains(x)             包含
//   IsNull() / NotNull()    指针 null/非 null
//   Field(&T::m, m_)        结构体字段匹配

// ==================== gMock 模式结束 ====================

// ==================== 20. 访问 protected/private 成员（仅测试文件内，不改源码） ====================
//
// 方法 1：#define 预处理（最常用，推荐）
// #define 必须在 #include 之前，且 #undef 紧随其后防止污染
//
// #define protected public
// #define private public
// #include "myclass.h"
// #undef protected
// #undef private
//
// 注意：此方法仅用于测试文件中暴露被测类的 protected/private 成员，
// 不适用于修改第三方头文件的访问级别（可能导致 ODR 违规）。
// Iron Law #9 禁止修改源码，因此方法 2（FRIEND_TEST）不适用。
//
// 方法 2：测试友元（需源码加 FRIEND_TEST 声明，但 Iron Law #9 禁止改源码，此方法不适用）
// class MyClass {
//     FRIEND_TEST(MyClassTest, TestPrivateMethod);  // 需在源码中添加
//     ...
// };

// ==================== Stub 模式结束 ====================
