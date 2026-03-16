# -*- coding: utf-8 -*-
"""
🖥️ Koto 系统信息收集器

这个模块负责收集和管理主机系统的各种信息，
使 Koto 能够像真正的本地助手一样，理解用户的计算环境。

Features:
  - 实时 CPU、内存、磁盘监控
  - 进程和应用检测
  - Python 环境信息
  - 网络状态
  - 智能缓存和更新机制
"""

import os
import sys
import time
import platform
import psutil
import socket
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional
import json
import logging

# 尝试导入可选的依赖

logger = logging.getLogger(__name__)

try:
    import wmi
    HAS_WMI = True
except ImportError:
    HAS_WMI = False


class SystemInfoCollector:
    """
    系统信息收集器
    
    提供一个统一的接口来获取系统的各种信息。
    使用缓存机制避免频繁的系统调用。
    """
    
    def __init__(self, cache_timeout: float = 5.0):
        """
        初始化收集器
        
        Args:
            cache_timeout: 缓存超时时间（秒），默认 5 秒
        """
        global HAS_WMI
        
        self.cache_timeout = cache_timeout
        self.cache: Dict[str, tuple] = {}  # {key: (data, timestamp)}
        self._wmi_conn = None
        
        # 初始化 WMI（如果可用）
        if HAS_WMI:
            try:
                self._wmi_conn = wmi.WMI()
            except Exception:
                HAS_WMI = False
    
    def _get_cached(self, key: str, ttl: Optional[float] = None) -> Optional[Any]:
        """获取缓存数据"""
        if key not in self.cache:
            return None
        
        data, timestamp = self.cache[key]
        timeout = ttl if ttl is not None else self.cache_timeout
        
        if time.time() - timestamp < timeout:
            return data
        
        del self.cache[key]
        return None
    
    def _set_cached(self, key: str, data: Any) -> None:
        """设置缓存数据"""
        self.cache[key] = (data, time.time())
    
    def get_cpu_info(self) -> Dict[str, Any]:
        """获取 CPU 信息"""
        cached = self._get_cached('cpu_info', ttl=2)
        if cached:
            return cached
        
        try:
            cpu_percent = psutil.cpu_percent(interval=0.1)
            cpu_count = psutil.cpu_count(logical=False)
            cpu_count_logical = psutil.cpu_count(logical=True)
            
            # 尝试获取 CPU 型号
            cpu_model = platform.processor() or "Unknown"
            
            # 尝试获取 CPU 频率
            cpu_freq = psutil.cpu_freq()
            freq_mhz = cpu_freq.current if cpu_freq else 0
            
            info = {
                'usage_percent': cpu_percent,
                'physical_cores': cpu_count,
                'logical_cores': cpu_count_logical,
                'model': cpu_model,
                'frequency_mhz': round(freq_mhz, 1),
                'load_average': os.getloadavg() if hasattr(os, 'getloadavg') else (0, 0, 0)
            }
            
            self._set_cached('cpu_info', info)
            return info
            
        except Exception as e:
            logger.warning(f"[SystemInfo] Warning: Failed to get CPU info: {e}")
            return {
                'usage_percent': 0,
                'physical_cores': 0,
                'logical_cores': 0,
                'model': 'Unknown',
                'frequency_mhz': 0,
                'load_average': (0, 0, 0),
                'error': str(e)
            }
    
    def get_memory_info(self) -> Dict[str, Any]:
        """获取内存信息"""
        cached = self._get_cached('memory_info', ttl=2)
        if cached:
            return cached
        
        try:
            memory = psutil.virtual_memory()
            swap = psutil.swap_memory()
            
            info = {
                'total_gb': round(memory.total / (1024**3), 2),
                'used_gb': round(memory.used / (1024**3), 2),
                'available_gb': round(memory.available / (1024**3), 2),
                'percent': memory.percent,
                'swap_total_gb': round(swap.total / (1024**3), 2),
                'swap_used_gb': round(swap.used / (1024**3), 2),
                'swap_percent': swap.percent
            }
            
            self._set_cached('memory_info', info)
            return info
            
        except Exception as e:
            logger.warning(f"[SystemInfo] Warning: Failed to get memory info: {e}")
            return {
                'total_gb': 0,
                'used_gb': 0,
                'available_gb': 0,
                'percent': 0,
                'error': str(e)
            }
    
    def get_disk_info(self) -> Dict[str, Any]:
        """获取磁盘信息"""
        cached = self._get_cached('disk_info', ttl=10)
        if cached:
            return cached
        
        try:
            disks: Dict[str, Any] = {}
            
            # 获取所有磁盘分区
            partitions = psutil.disk_partitions()
            
            for partition in partitions:
                # 跳过系统分区
                if partition.fstype == '' or 'loop' in partition.device:
                    continue
                
                try:
                    usage = psutil.disk_usage(partition.mountpoint)
                    drive_letter = partition.device.split('\\')[0] if '\\' in partition.device else partition.device
                    
                    disks[drive_letter] = {
                        'mount': partition.mountpoint,
                        'fstype': partition.fstype,
                        'total_gb': round(usage.total / (1024**3), 2),
                        'used_gb': round(usage.used / (1024**3), 2),
                        'free_gb': round(usage.free / (1024**3), 2),
                        'percent': usage.percent
                    }
                except (OSError, PermissionError):
                    continue
            
            # 计算总计
            total_gb = sum(d.get('total_gb', 0) for d in disks.values())
            free_gb = sum(d.get('free_gb', 0) for d in disks.values())
            
            info = {
                'drives': disks,
                'total_gb': round(total_gb, 2),
                'free_gb': round(free_gb, 2),
                'percent_full': round(100 - (free_gb / total_gb * 100) if total_gb > 0 else 0, 1)
            }
            
            self._set_cached('disk_info', info)
            return info
            
        except Exception as e:
            logger.warning(f"[SystemInfo] Warning: Failed to get disk info: {e}")
            return {
                'drives': {},
                'total_gb': 0,
                'free_gb': 0,
                'error': str(e)
            }
    
    def get_network_info(self) -> Dict[str, Any]:
        """获取网络信息"""
        cached = self._get_cached('network_info', ttl=5)
        if cached:
            return cached
        
        try:
            info = {
                'hostname': socket.gethostname(),
                'interfaces': {}
            }
            
            # 获取所有网络接口
            if_addrs = psutil.net_if_addrs()
            
            for interface_name, interface_addrs in if_addrs.items():
                info['interfaces'][interface_name] = {
                    'ipv4': None,
                    'ipv6': None,
                    'mac': None
                }
                
                for addr in interface_addrs:
                    if addr.family == socket.AF_INET:
                        info['interfaces'][interface_name]['ipv4'] = addr.address
                    elif addr.family == socket.AF_INET6:
                        info['interfaces'][interface_name]['ipv6'] = addr.address
                    elif addr.family == psutil.AF_LINK:
                        info['interfaces'][interface_name]['mac'] = addr.address
            
            # 获取网络连接统计
            if_stats = psutil.net_if_stats()
            info['connection_status'] = {
                name: {
                    'is_up': stats.isup,
                    'speed': stats.speed,
                    'mtu': stats.mtu
                }
                for name, stats in if_stats.items()
            }
            
            self._set_cached('network_info', info)
            return info
            
        except Exception as e:
            logger.warning(f"[SystemInfo] Warning: Failed to get network info: {e}")
            return {
                'hostname': socket.gethostname(),
                'interfaces': {},
                'error': str(e)
            }
    
    def get_running_processes(self, top_n: int = 10) -> Dict[str, Any]:
        """获取运行中的进程（按内存占用排序）"""
        cached = self._get_cached('running_processes', ttl=3)
        if cached:
            return cached
        
        try:
            processes = []
            
            for proc in psutil.process_iter(['pid', 'name', 'memory_percent', 'cpu_percent']):
                try:
                    pinfo = proc.as_dict(attrs=['pid', 'name', 'memory_percent', 'cpu_percent'])
                    processes.append(pinfo)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            
            # 按内存占用排序
            processes = sorted(processes, key=lambda x: x.get('memory_percent', 0), reverse=True)
            
            info = {
                'total_processes': len(psutil.pids()),
                'top_processes': processes[:top_n],
                'key_processes': {
                    'python': [p for p in processes if 'python' in p['name'].lower()][:3],
                    'koto': [p for p in processes if 'koto' in p['name'].lower()],
                    'vscode': [p for p in processes if 'code' in p['name'].lower()],
                    'browser': [p for p in processes if any(x in p['name'].lower() for x in ['chrome', 'firefox', 'edge'])][:2]
                }
            }
            
            self._set_cached('running_processes', info)
            return info
            
        except Exception as e:
            logger.warning(f"[SystemInfo] Warning: Failed to get process info: {e}")
            return {
                'total_processes': 0,
                'top_processes': [],
                'error': str(e)
            }
    
    def get_python_environment(self) -> Dict[str, Any]:
        """获取 Python 环境信息"""
        cached = self._get_cached('python_environment', ttl=30)
        if cached:
            return cached
        
        try:
            import subprocess
            
            info = {
                'version': platform.python_version(),
                'executable': sys.executable,
                'implementation': platform.python_implementation(),
                'path': sys.prefix,
                'is_virtual_env': hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix),
                'virtual_env_name': os.environ.get('VIRTUAL_ENV', '').split(os.sep)[-1] or None
            }
            
            # 尝试获取已安装的包数 - 更高效的方法
            try:
                result = subprocess.run(
                    [sys.executable, '-m', 'pip', 'list', '--format=json'],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    packages = json.loads(result.stdout)
                    info['installed_packages_count'] = len(packages)
                    # 获取关键包版本
                    info['key_packages'] = {}
                    for pkg in packages:
                        if pkg['name'].lower() in ['flask', 'google-generativeai', 'psutil', 'requests', 'pillow']:
                            info['key_packages'][pkg['name']] = pkg['version']
            except Exception:
                info['installed_packages_count'] = 'unknown'
            
            self._set_cached('python_environment', info)
            return info
            
        except Exception as e:
            logger.warning(f"[SystemInfo] Warning: Failed to get Python environment info: {e}")
            return {
                'version': platform.python_version(),
                'executable': sys.executable,
                'error': str(e)
            }
    
    def get_top_processes(self, limit: int = 5) -> List[Dict[str, Any]]:
        """获取最耗资源的前N个进程"""
        cached = self._get_cached('top_processes', ttl=3)
        if cached:
            return cached[:limit]
        
        try:
            processes = self.get_running_processes(top_n=limit)
            top_list = processes.get('top_processes', [])
            self._set_cached('top_processes', top_list)
            return top_list[:limit]
        except Exception as e:
            logger.warning(f"[SystemInfo] Warning: Failed to get top processes: {e}")
            return []
    
    def get_installed_apps(self) -> List[str]:
        """获取已安装的关键应用列表"""
        cached = self._get_cached('installed_apps', ttl=30)
        if cached:
            return cached
        
        try:
            apps = []
            
            # Windows 特定的检测方式
            if HAS_WMI:
                try:
                    import wmi
                    c = wmi.WMI()
                    for item in c.Win32_Product():
                        apps.append(item.Name)
                except Exception:
                    pass
            
            # 备用方式：检查常见的可执行文件和注册表项
            common_apps = [
                ('Python', 'python.exe'),
                ('Node.js', 'node.exe'),
                ('Git', 'git.exe'),
                ('VS Code', 'code.exe'),
                ('Visual Studio', 'devenv.exe'),
                ('Chrome', 'chrome.exe'),
                ('Firefox', 'firefox.exe'),
                ('Anaconda', 'conda.exe'),
            ]
            
            import shutil
            for app_name, exe_name in common_apps:
                if shutil.which(exe_name) or self._check_program_files(exe_name):
                    if app_name not in apps:
                        apps.append(app_name)
            
            # 如果没有找到任何应用，返回一个基本列表
            if not apps:
                apps = ['Python', 'Windows']  # 最少的默认值
            
            self._set_cached('installed_apps', apps)
            return apps
            
        except Exception as e:
            logger.warning(f"[SystemInfo] Warning: Failed to get installed apps: {e}")
            return ['Python', 'Windows']
    
    def _check_program_files(self, exe_name: str) -> bool:
        """检查程序文件夹中是否存在可执行文件"""
        try:
            import glob
            program_files = [
                'C:\\Program Files',
                'C:\\Program Files (x86)',
                os.path.expandvars('%PROGRAMFILES%'),
                os.path.expandvars('%PROGRAMFILES(X86)%'),
            ]
            
            for pf in program_files:
                if os.path.exists(pf):
                    found = glob.glob(f"{pf}/*/{exe_name}")
                    if found:
                        return True
            
            return False
        except Exception:
            return False
    
    def get_system_state(self) -> Dict[str, Any]:
        """获取系统整体状态（快速摘要）"""
        return {
            'timestamp': datetime.now().isoformat(),
            'cpu': self.get_cpu_info(),
            'memory': self.get_memory_info(),
            'disk': self.get_disk_info(),
            'processes': self.get_running_processes(top_n=5),
            'network': self.get_network_info(),
            'python': self.get_python_environment()
        }
    
    def get_formatted_info(self, include_top_processes: bool = True) -> str:
        """获取格式化的系统信息（用于系统指令）"""
        cpu = self.get_cpu_info()
        memory = self.get_memory_info()
        disk = self.get_disk_info()
        processes = self.get_running_processes(top_n=5) if include_top_processes else {}
        network = self.get_network_info()
        python = self.get_python_environment()
        
        # 构建格式化字符串
        lines = []
        
        # CPU 信息
        lines.append("📊 **CPU 状态**:")
        lines.append(f"  • 使用率: {cpu['usage_percent']}%")
        lines.append(f"  • 核心: {cpu['physical_cores']} 物理 / {cpu['logical_cores']} 逻辑")
        lines.append(f"  • 型号: {cpu['model']}")
        
        # 内存信息
        lines.append("\n🧠 **内存状态**:")
        lines.append(f"  • 使用: {memory['used_gb']}GB / {memory['total_gb']}GB ({memory['percent']}%)")
        lines.append(f"  • 可用: {memory['available_gb']}GB")
        if memory['swap_total_gb'] > 0:
            lines.append(f"  • 虚拟内存: {memory['swap_used_gb']}GB / {memory['swap_total_gb']}GB ({memory['swap_percent']}%)")
        
        # 磁盘信息
        lines.append("\n💿 **磁盘状态**:")
        if disk.get('drives'):
            for drive, info in disk['drives'].items():
                lines.append(f"  • {drive}: {info['used_gb']}GB / {info['total_gb']}GB ({info['percent']}%)")
            lines.append(f"  • 总剩余空间: {disk['free_gb']}GB")
        
        # 网络信息
        lines.append("\n🌐 **网络状态**:")
        lines.append(f"  • 主机名: {network.get('hostname', 'Unknown')}")
        if network.get('interfaces'):
            ipv4_addrs = [
                addr['ipv4'] for addr in network['interfaces'].values()
                if addr.get('ipv4') and not addr['ipv4'].startswith('127.')
            ]
            if ipv4_addrs:
                lines.append(f"  • IP 地址: {', '.join(ipv4_addrs)}")
        
        # 进程信息
        if include_top_processes and processes.get('top_processes'):
            lines.append("\n🚀 **最耗内存的进程**:")
            for proc in processes['top_processes'][:3]:
                lines.append(f"  • {proc['name']}: {proc['memory_percent']}% 内存")
        
        # Python 环境
        lines.append("\n🐍 **Python 环境**:")
        lines.append(f"  • 版本: {python['version']}")
        lines.append(f"  • 路径: {python['executable'][:50]}...")
        if python.get('is_virtual_env'):
            lines.append(f"  • 虚拟环境: {python.get('virtual_env_name', '活跃')}")
        if python.get('installed_packages_count'):
            lines.append(f"  • 已安装包数: {python['installed_packages_count']}")
        
        return "\n".join(lines)
    
    def get_system_warnings(self) -> List[str]:
        """检查系统状态并返回警告列表"""
        warnings = []
        
        try:
            cpu = self.get_cpu_info()
            if cpu['usage_percent'] > 90:
                warnings.append(f"⚠️ CPU 使用率过高 ({cpu['usage_percent']}%)")
            
            memory = self.get_memory_info()
            if memory['percent'] > 90:
                warnings.append(f"⚠️ 内存使用率过高 ({memory['percent']}%)")
            elif memory['percent'] > 75:
                warnings.append(f"🟡 内存使用率较高 ({memory['percent']}%)")
            
            disk = self.get_disk_info()
            if disk['percent_full'] > 90:
                warnings.append(f"⚠️ 磁盘空间不足 (剩余 {disk['free_gb']}GB)")
            elif disk['percent_full'] > 80:
                warnings.append(f"🟡 磁盘空间有限 (剩余 {disk['free_gb']}GB)")
            
        except Exception:
            pass
        
        return warnings


# 全局单例实例
_collector_instance: Optional[SystemInfoCollector] = None


def get_system_info_collector() -> SystemInfoCollector:
    """获取系统信息收集器的单例实例"""
    global _collector_instance
    if _collector_instance is None:
        _collector_instance = SystemInfoCollector(cache_timeout=5)
    return _collector_instance


# 便利函数
def get_system_info() -> Dict[str, Any]:
    """获取完整的系统信息"""
    return get_system_info_collector().get_system_state()


def get_formatted_system_info(include_processes: bool = True) -> str:
    """获取格式化的系统信息"""
    return get_system_info_collector().get_formatted_info(include_top_processes=include_processes)


def get_system_warnings() -> List[str]:
    """获取系统警告"""
    return get_system_info_collector().get_system_warnings()


if __name__ == '__main__':
    # 测试脚本
    logger.info("🖥️ Koto 系统信息收集器")
    logger.info("=" * 60)
    
    collector = get_system_info_collector()
    
    # 打印格式化信息
    logger.info(collector.get_formatted_info())
    
    # 打印警告
    warnings = collector.get_system_warnings()
    if warnings:
        logger.info("\n" + "=" * 60)
        logger.warning("⚠️ 系统警告:")
        for warning in warnings:
            logger.info(f"  {warning}")
