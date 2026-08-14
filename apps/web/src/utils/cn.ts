/**
 * @description 静态 UnoCSS 类名合并工具
 * 仅组合完整候选类名，禁止通过本工具生成运行时动态 Utility。
 */
import { clsx, type ClassValue } from "clsx";

/**
 * 合并静态类名和受控条件类名。
 *
 * UnoCSS 只能可靠提取源代码中出现的完整类名，调用方不得通过本函数拼接
 * `bg-${color}` 等运行时 Utility；动态视觉状态必须使用显式完整类名映射。
 *
 * @param inputs 静态类名、条件对象或嵌套类名集合
 * @returns 过滤无效值后的类名字符串
 */
export function cn(...inputs: ClassValue[]): string {
  return clsx(inputs);
}
