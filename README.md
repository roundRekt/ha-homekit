# HomeKit Bridge

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/default)

针对 Home Assistant 官方内置 HomeKit Bridge（家庭桥接）集成的增强版本。主要解决了官方组件中**除湿机/新风一体机无法在 Apple HomeKit（苹果家庭）中调节风扇转速**以及**模式被写死无法选择自动（Auto）模式**的痛点。

---

## 🌟 核心特性

1. **除湿机风扇转速调节（RotationSpeed）**
   - 苹果原生 HomeKit HAP 规范允许在 `HumidifierDehumidifier` 服务中添加可选的 `RotationSpeed` 特征值。
   - 官方 HomeKit Bridge 未实现该特征；本组件通过**自动寻找同一设备关联的 `fan` 实体**，为苹果家庭中的除湿机卡片补充 **0%~100%（例如 1~6 档）风扇调速滑块**，控制指令与转速状态实时双向同步。
2. **解锁原生自动模式（Auto Mode）**
   - 官方组件在 `type_humidifiers.py` 中将除湿机的目标状态强制写死为 `min=2, max=2`（只允许除湿），拒绝并隐藏了 HomeKit 原生支持的 `0 (Auto / 自动)`。
   - 本组件自动检测实体及其关联设备是否支持 `auto` 模式，若支持则开放 HomeKit 原生自动模式：
     - 选择 **自动** 时：下发模式 `auto` 到除湿机及新风机。
     - 选择 **除湿** 时：下发模式 `dehumidify`。
3. **开箱即用，零配置自动发现（Smart Auto-Discovery）**
   - 桥接除湿机（如 `humidifier.song_jing_xin_feng_chu_shi`）时，自动通过 Home Assistant 实体注册表识别同属于一个物理设备的 `fan` 实体。
   - 同时也支持通过自定义参数手动指定关联实体。

---

## 📦 安装方法

### 方式一：通过 HACS 安装
1. 打开 Home Assistant 的 **HACS**。
2. 点击右上角菜单，选择 **自定义存储库 (Custom repositories)**。
3. 输入本仓库地址，类别选择 **集成 (Integration)** 并添加。
4. 在集成列表中搜索并下载 **HomeKit Bridge**。
5. 重启 Home Assistant。

### 方式二：手动安装
1. 下载本仓库源码。
2. 将 `custom_components/homekit` 整个目录复制到 Home Assistant 配置目录的 `custom_components/` 下：
   ```
   /config/custom_components/homekit/
   ```
3. 重启 Home Assistant。

---

## ⚙️ 配置说明

### 1. 自动关联模式（推荐，无需额外配置）
当您的设备（如新风除湿机）在 HA 中同时生成了 `humidifier.xxx` 与 `fan.xxx` 且属于同一设备时，组件会自动将风扇转速与模式进行绑定，您在苹果家庭中添加该除湿机后即可看到风速滑块与自动模式切换。

### 2. 手动指定关联风扇（可选）
若您的风扇和除湿机是不同集成或虚拟生成的实体，可在 HomeKit 桥接条目的实体高级配置或 YAML 中指定：
```yaml
homekit:
  - filter:
      include_entities:
        - humidifier.song_jing_xin_feng_chu_shi
    entity_config:
      humidifier.song_jing_xin_feng_chu_shi:
        linked_fan_entity: fan.song_jing_xin_feng_chu_shi
```

---

## 📱 苹果家庭效果

- **模式选项**：除湿 / 自动
- **目标湿度调节**：20% ~ 100% 湿度滑块
- **风速调节**：0% ~ 100% 转速滑块（按底层步长如 1~6 档调节）
- **当前状态**：显示当前室内湿度及运行状态
