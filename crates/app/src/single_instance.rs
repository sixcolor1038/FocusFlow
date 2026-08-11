//! 单实例检查（Windows 命名互斥体）。

/// 检查是否已有实例在运行。
///
/// 若已有实例：返回 false（由调用方激活已有窗口后退出）。
/// 若本实例是唯一的：返回 true 并持有互斥体句柄（进程生命周期内保持）。
pub fn check_single_instance() -> bool {
    #[cfg(windows)]
    {
        use windows::Win32::Foundation::{ERROR_ALREADY_EXISTS, GetLastError};
        use windows::Win32::System::Threading::CreateMutexW;

        // 宽字符互斥名："Global\FocusFlowAppMutex"
        const NAME: &[u16] = &[
            'G' as u16, 'l' as u16, 'o' as u16, 'b' as u16, 'a' as u16, 'l' as u16,
            '\\' as u16, 'F' as u16, 'o' as u16, 'c' as u16, 'u' as u16, 's' as u16,
            'F' as u16, 'l' as u16, 'o' as u16, 'w' as u16, 'A' as u16, 'p' as u16,
            'p' as u16, 'M' as u16, 'u' as u16, 't' as u16, 'e' as u16, 'x' as u16, 0,
        ];
        unsafe {
            let name_ptr = windows::core::PCWSTR::from_raw(NAME.as_ptr());
            match CreateMutexW(None, true, name_ptr) {
                Ok(mutex) => {
                    let err = GetLastError();
                    if err == ERROR_ALREADY_EXISTS {
                        return false;
                    }
                    // 持有句柄直到进程退出（放入泄漏的 Box，防止 Drop 释放句柄）
                    let _leaked = Box::leak(Box::new(mutex));
                    true
                }
                Err(_) => {
                    // 互斥体创建失败（权限等）：保守放行
                    true
                }
            }
        }
    }
    #[cfg(not(windows))]
    {
        true
    }
}
