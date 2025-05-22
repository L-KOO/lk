import os
import sys
import time
import shutil
import logging
import argparse
import subprocess
import tempfile
import re
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# 配置日志
logging.basicConfig(
    filename='monitor.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 自动监控脚本所在目录的配置
AUTO_MONITOR_SCRIPT_DIR = False

def create_media_directory():
    """创建脚本目录下的media文件夹（如果不存在）"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    media_dir = os.path.join(script_dir, 'media')
    
    try:
        if not os.path.exists(media_dir):
            os.makedirs(media_dir)
            print(f"创建媒体目录: {media_dir}")
        return media_dir
    except Exception as e:
        print(f"无法创建媒体目录: {str(e)}")
        logger.error(f"无法创建媒体目录: {str(e)}")
        return None

def ask_for_auto_monitor():
    """询问用户是否开启自动监控模式"""
    print("\n是否开启自动监控模式？")
    print("开启后，程序将自动监控指定目录并处理新添加的媒体文件")
    
    while True:
        response = input("开启自动监控？(y/n): ").strip().lower()
        if response == 'y':
            return True
        elif response == 'n':
            return False
        else:
            print("请输入 y 或 n")

def extract_and_rename_file(path):
    """提取文件名并处理特殊字符，返回新路径和是否重命名的标志"""
    # 处理URL情况
    if path.startswith(('http://', 'https://')):
        return path, False, os.path.basename(path)  # 返回原始文件名
    
    if not os.path.exists(path):
        return path, False, os.path.basename(path)
    
    dirname = os.path.dirname(path)
    filename = os.path.basename(path)
    
    # 定义需要删除的违规字符列表
    invalid_chars = [' ', '!', '?', '(', ')', '[', ']', '{', '}', '&', '#', '%', '$', '*', '+', ',', '/', ':', ';', '=', '<', '>', '?', '@', '\\', '^', '|', '~']
    
    # 移除违规字符
    new_filename = ''.join(c for c in filename if c not in invalid_chars)
    
    if new_filename != filename:
        new_path = os.path.join(dirname, new_filename)
        try:
            os.rename(path, new_path)
            print(f"✂ 已清理文件名: {filename} → {new_filename}")
            return new_path, True, filename  # 返回原始文件名
        except Exception as e:
            print(f"警告: 无法重命名文件 {filename}: {str(e)}")
            return path, False, filename
    return path, False, filename

def generate_docker_command(media_path, gpu_base_template, cpu_base_template, use_gpu=True):
    """生成Docker命令，修复路径拼接错误"""
    if not media_path:
        return None
    
    base_template = gpu_base_template if use_gpu else cpu_base_template
    
    if media_path.startswith(('http://', 'https://')):
        return base_template + f'"{media_path}"'
    
    media_path = os.path.abspath(media_path)
    filename = os.path.basename(media_path)
    
    # 修复双/data问题和空格问题
    clean_base_template = base_template.rstrip() + ' '
    full_command = f"{clean_base_template}--media=/data/{filename}"
    
    # 检测并记录命令中的潜在问题
    if '  ' in full_command:
        logger.warning(f"命令中存在连续空格: {full_command}")
    
    return full_command

def execute_docker_command(command):
    """执行Docker命令，添加命令验证，确保容器完全退出"""
    # 验证命令格式
    if not command.startswith('docker run'):
        logger.error(f"无效的Docker命令格式: {command}")
        print("✘ 错误: 生成的命令格式不正确")
        return False
    
    # 检查关键参数
    if '--media=' not in command:
        logger.error(f"命令缺少--media参数: {command}")
        print("✘ 错误: 生成的命令缺少--media参数")
        return False
    
    print(f"\n执行命令: {command}")
    
    try:
        # 使用subprocess.Popen执行命令，获取进程对象
        process = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # 实时输出标准输出
        for line in iter(process.stdout.readline, ''):
            print(line.strip())
        
        # 等待进程完成并获取返回码
        return_code = process.wait()
        
        # 读取标准错误
        stderr = process.stderr.read()
        if stderr:
            print(f"命令错误输出: {stderr}")
        
        # 检查容器是否已退出
        if return_code == 0:
            print("✔ Docker容器已成功退出")
            return True
        else:
            print(f"✘ Docker容器以非零状态码退出: {return_code}")
            logger.error(f"命令执行失败，返回码: {return_code}, 错误: {stderr}")
            return False
            
    except Exception as e:
        print(f"执行命令时出错: {str(e)}")
        logger.error(f"执行命令时出错: {str(e)}")
        return False

def find_generated_files(original_filename, media_dir):
    """查找所有与原文件名相关的生成文件"""
    # 获取原始文件名（不带扩展名）
    original_name, _ = os.path.splitext(original_filename)
    
    # 获取media目录的上级目录
    parent_dir = os.path.dirname(media_dir)
    
    # 查找所有匹配的文件
    generated_files = []
    
    # 定义可能的扩展名列表
    possible_extensions = [
        '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm',
        '.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg', '.wma',
        '.srt', '.ass', '.txt', '.json'
    ]
    
    # 检查上级目录中的所有文件
    for root, _, files in os.walk(parent_dir):
        for file in files:
            # 跳过media目录内部的文件
            if media_dir in root:
                continue
                
            # 检查文件名是否包含原始名称和可能的扩展名
            if original_name in file:
                for ext in possible_extensions:
                    if file.endswith(ext):
                        generated_files.append(os.path.join(root, file))
                        break
    
    return generated_files

def create_destination_directory(media_dir, original_filename):
    """创建用于存放处理后文件的目标目录"""
    # 获取原始文件名（不带扩展名）
    original_name, _ = os.path.splitext(original_filename)
    
    # 构建目标目录路径
    dest_dir = os.path.join(media_dir, original_name)
    
    # 创建目录（如果不存在）
    try:
        if not os.path.exists(dest_dir):
            os.makedirs(dest_dir)
            print(f"📁 创建目标目录: {dest_dir}")
        return dest_dir
    except Exception as e:
        print(f"警告: 无法创建目标目录 {dest_dir}: {str(e)}")
        return None

def check_and_rename_file(dest_dir, filename):
    """检查目标目录中是否存在同名文件，如有则添加序号"""
    base_name, ext = os.path.splitext(filename)
    
    # 尝试匹配特殊格式：2025-05-16懒銀直播录像-1.2025-05-16懒銀直播录像Av114516042056977P1_歌曲名字
    match = re.match(r'(\d{4}-\d{2}-\d{2}.*?-\d+\.\d{4}-\d{2}-\d{2}.*?)(_.*)', base_name)
    if match:
        base_part = match.group(1)
        song_part = match.group(2)
    else:
        base_part = base_name
        song_part = ''
    
    # 检查文件是否存在，不存在则直接返回
    if not os.path.exists(os.path.join(dest_dir, filename)):
        return filename
    
    # 文件已存在，添加序号
    counter = 1
    new_filename = f"{base_part}_{counter}{song_part}{ext}"
    
    while os.path.exists(os.path.join(dest_dir, new_filename)):
        counter += 1
        new_filename = f"{base_part}_{counter}{song_part}{ext}"
    
    print(f"⚠ 发现重名文件，将使用: {new_filename}")
    return new_filename

def move_generated_files(generated_files, dest_dir, original_filename, simple_filename):
    """将生成的文件移动到目标目录，并恢复原始文件名"""
    if not generated_files or not dest_dir:
        return
    
    print(f"\n🚚 移动生成的文件到 {dest_dir}")
    
    # 获取原始文件名（不带扩展名）
    original_name, original_ext = os.path.splitext(original_filename)
    
    # 获取简单文件名（不带扩展名）
    simple_name, _ = os.path.splitext(simple_filename)
    
    for file_path in generated_files:
        try:
            # 获取当前文件名和扩展名
            current_filename = os.path.basename(file_path)
            current_name, current_ext = os.path.splitext(current_filename)
            
            # 替换简单名称为原始名称，保留其他部分
            if simple_name in current_name:
                new_name = current_name.replace(simple_name, original_name)
                new_filename = new_name + current_ext
            else:
                # 如果简单名称不在当前文件名中，直接添加原始名称前缀
                new_filename = f"{original_name}_{current_filename}"
            
            # 检查并处理重名文件
            new_filename = check_and_rename_file(dest_dir, new_filename)
            
            # 构建目标路径
            dest_path = os.path.join(dest_dir, new_filename)
            
            # 移动文件
            shutil.move(file_path, dest_path)
            print(f"✓ 移动: {current_filename} → {new_filename}")
        except Exception as e:
            print(f"✘ 无法移动文件 {file_path}: {str(e)}")

class MediaHandler(FileSystemEventHandler):
    """文件系统事件处理类"""
    def __init__(self, gpu_base_template, cpu_base_template, media_dir):
        self.gpu_base_template = gpu_base_template
        self.cpu_base_template = cpu_base_template
        self.processed_files = set()  # 记录已处理的文件
        self.media_dir = media_dir

    def on_created(self, event):
        """文件创建事件处理"""
        if not event.is_directory:
            file_path = event.src_path
            print(f"\n📁 检测到新文件: {file_path}")
            
            # 等待文件写入完成
            if not self._wait_for_file_stability(file_path):
                print(f"→ 文件 {os.path.basename(file_path)} 不稳定，跳过处理")
                return
            
            # 检查文件是否已处理
            file_key = (file_path, os.path.getmtime(file_path))
            if file_key in self.processed_files:
                print(f"→ 文件 {os.path.basename(file_path)} 已处理，跳过")
                return
            
            # 检查文件是否为媒体文件
            if self._is_media_file(file_path):
                print(f"→ 新的媒体文件，开始处理...")
                self._process_media_file(file_path)
                self.processed_files.add(file_key)
            else:
                print(f"→ {os.path.basename(file_path)} 不是媒体文件，跳过")

    def _wait_for_file_stability(self, file_path, timeout=60, interval=2):
        """等待文件稳定（不再变化），增加了更健壮的错误处理"""
        start_time = time.time()
        stable_count = 0  # 连续检查文件大小相同的次数
        
        while time.time() - start_time < timeout:
            try:
                if not os.path.exists(file_path):
                    print(f"文件 {file_path} 不存在，可能已被删除")
                    return False
                    
                current_size = os.path.getsize(file_path)
                
                if current_size == 0:
                    # 文件大小为0，可能还在写入
                    print(f"文件 {os.path.basename(file_path)} 大小为0，仍在写入中...")
                    stable_count = 0
                elif current_size == getattr(self, '_last_size', None):
                    stable_count += 1
                    if stable_count >= 3:  # 连续3次检查大小相同，认为文件已稳定
                        print(f"文件 {os.path.basename(file_path)} 已稳定，准备处理")
                        return True
                else:
                    stable_count = 0
                    self._last_size = current_size
                
                print(f"等待文件 {os.path.basename(file_path)} 稳定中... {time.time()-start_time:.1f}秒")
                time.sleep(interval)
                
            except Exception as e:
                print(f"检查文件稳定性时出错: {str(e)}，等待重试")
                time.sleep(interval)
        
        print(f"警告: 文件 {file_path} 在 {timeout} 秒内未稳定，将尝试处理")
        return False

    def _is_media_file(self, file_path):
        """检查文件是否为媒体文件"""
        media_extensions = [
            '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm',
            '.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg', '.wma'
        ]
        
        ext = os.path.splitext(file_path)[1].lower()
        return ext in media_extensions

    def _process_media_file(self, file_path):
        """处理媒体文件，包含重命名和移动生成文件的逻辑"""
        original_file_path = file_path  # 保存原始文件路径
        simple_file_path = None         # 简化后的文件路径
        original_filename = None        # 原始文件名
        
        try:
            # 验证文件存在性
            if not os.path.exists(file_path):
                raise ValueError(f"文件不存在: {file_path}")
            
            # 处理文件并重命名（清理违规字符），获取原始文件名
            file_path, was_renamed, original_filename = extract_and_rename_file(file_path)
            
            # 创建一个更简单的临时文件名（用于处理）
            base_name, ext = os.path.splitext(os.path.basename(file_path))
            simple_name = f"temp_{time.time_ns()}"
            simple_filename = f"{simple_name}{ext}"
            simple_file_path = os.path.join(os.path.dirname(file_path), simple_filename)
            
            # 重命名文件为简单名称
            os.rename(file_path, simple_file_path)
            print(f"📛 临时重命名: {os.path.basename(file_path)} → {simple_filename}")
            
            # 生成并执行Docker命令（默认启用GPU）
            command = generate_docker_command(simple_file_path, self.gpu_base_template, self.cpu_base_template, True)
            if command:
                print(f"生成命令: {command}")
                print(f"📌 原始文件路径: {original_file_path}")
                print(f"📌 简化后文件路径: {simple_file_path}")
                print(f"📌 Docker命令: {command}")
                
                # 执行Docker命令并等待容器完全退出
                if execute_docker_command(command):
                    print(f"✔ 执行成功: {simple_file_path}")
                    
                    # 等待所有容器退出（确保没有残留容器）
                    self._wait_for_all_containers_to_exit()
                    
                    # 查找所有生成的文件
                    generated_files = find_generated_files(simple_name, self.media_dir)
                    print(f"🔍 找到 {len(generated_files)} 个生成的文件")
                    
                    # 创建目标目录
                    dest_dir = create_destination_directory(self.media_dir, original_filename)
                    
                    # 移动生成的文件到目标目录，并恢复原始文件名
                    move_generated_files(generated_files, dest_dir, original_filename, simple_filename)
                    
                    # 将原始文件移动到目标目录
                    if os.path.exists(simple_file_path):
                        # 恢复原始文件名
                        final_file_path = os.path.join(os.path.dirname(simple_file_path), original_filename)
                        os.rename(simple_file_path, final_file_path)
                        
                        # 移动到目标目录
                        shutil.move(final_file_path, os.path.join(dest_dir, original_filename))
                        print(f"✓ 移动原始文件: {original_filename}")
                    else:
                        print(f"✘ 原始文件 {simple_file_path} 不存在，可能已被移动或删除")
                else:
                    print(f"✘ 执行失败: {simple_file_path}")
            else:
                print(f"✘ 命令生成失败: {file_path}")
                
        except Exception as e:
            print(f"✘ 处理出错: {str(e)}")
            logger.error(f"处理出错: {str(e)}")
            
            # 尝试恢复原始文件名（如果已更改）
            if simple_file_path and os.path.exists(simple_file_path) and original_file_path:
                try:
                    os.rename(simple_file_path, original_file_path)
                    print(f"⚠ 已恢复原始文件名: {os.path.basename(original_file_path)}")
                except Exception as e2:
                    print(f"⚠ 无法恢复原始文件名: {str(e2)}")
        finally:
            # 清理工作
            pass

    def _wait_for_all_containers_to_exit(self, max_retries=10, retry_interval=2):
        """等待所有Docker容器完全退出"""
        print("🕒 检查是否有运行中的Docker容器...")
        
        for attempt in range(max_retries):
            try:
                # 检查是否有运行中的容器
                result = subprocess.run(
                    "docker ps -q",
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                
                if result.returncode != 0:
                    print(f"✘ 检查容器时出错: {result.stderr}")
                    break
                
                container_ids = result.stdout.strip().split()
                
                if not container_ids:
                    print("✔ 没有检测到运行中的Docker容器")
                    return True
                
                print(f"检测到 {len(container_ids)} 个运行中的容器，等待 {retry_interval} 秒...")
                time.sleep(retry_interval)
                
            except Exception as e:
                print(f"✘ 检查容器时发生异常: {str(e)}")
                break
        
        print("⚠ 已达到最大重试次数，继续处理文件移动操作")
        return False

def monitor_directory(directory, gpu_base_template, cpu_base_template, media_dir):
    """监控指定目录中的新文件"""
    print(f"\n开始监控目录: {directory}")
    
    # 验证目录存在
    if not os.path.isdir(directory):
        print(f"错误: 监控目录不存在: {directory}")
        return
    
    # 创建事件处理程序
    event_handler = MediaHandler(gpu_base_template, cpu_base_template, media_dir)
    
    # 创建观察者
    observer = Observer()
    observer.schedule(event_handler, path=directory, recursive=False)
    
    # 启动观察者
    observer.start()
    print("监控已启动...")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
    print("\n监控已停止")

def main():
    # 创建媒体目录
    media_dir = create_media_directory()
    if not media_dir:
        print("无法创建媒体目录，程序退出")
        sys.exit(1)
    
    # 获取脚本所在目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    # 转换为Docker可识别的格式（处理Windows路径中的反斜杠）
    # 移除路径中的双引号
    docker_script_dir = script_dir.replace('\\', '/')
    docker_media_dir = media_dir.replace('\\', '/')
    
    # 定义GPU和CPU的Docker命令模板，修正参数格式
    gpu_base_template = (
        f'docker run --rm --gpus all -v {docker_script_dir}:/inaseg -v {docker_media_dir}:/data '
        f'gaoshi/ipynb-inaseg:nightly-gpu python /inaseg/inaseg.py '
        f'--shazam --shazam_multithread=2 --cleanup --outdir=/inaseg --aria=8'
    )
    cpu_base_template = (
        f'docker run --rm -v {docker_script_dir}:/inaseg -v {docker_media_dir}:/data '
        f'ipynb-inaseg python /inaseg/inaseg.py '
        f'--shazam --shazam_multithread=2 --cleanup --outdir=/inaseg --aria=8'
    )
    
    # 询问用户是否开启自动监控
    enable_auto_monitor = ask_for_auto_monitor()
    
    try:
        if enable_auto_monitor:
            # 自动监控media目录
            print(f"将自动监控媒体目录: {media_dir}")
            monitor_directory(media_dir, gpu_base_template, cpu_base_template, media_dir)
        else:
            # 未启用自动监控，进入手动模式
            print("\n已选择不启用自动监控，将进入手动模式")
            
            # 传统模式：处理命令行参数或用户输入
            print("\n===== Docker命令生成器 =====")
            
            # 获取GPU选项
            use_gpu = None
            while use_gpu not in ('y', 'n'):
                use_gpu = input("\n是否启用GPU支持？(y/n): ").strip().lower()
                if use_gpu == 'y':
                    print("→ 将使用GPU加速")
                elif use_gpu == 'n':
                    print("→ 将仅使用CPU")
                else:
                    print("请输入y或n")
            
            # 获取文件路径或URL
            print("\n方法1: 通过命令行参数传入文件路径或URL")
            print("方法2: 直接输入文件路径或URL")
            
            media_paths = []
            if len(sys.argv) > 1:
                media_paths = sys.argv[1:]
                print(f"\n已从命令行获取 {len(media_paths)} 个输入")
            else:
                print("\n请输入文件路径或URL（每行一个，输入空行结束）:")
                while True:
                    user_input = input().strip()
                    # 移除可能存在的引号
                    user_input = user_input.strip('"')
                    if not user_input:
                        break
                    
                    # 检查文件是否存在（本地路径）
                    if not user_input.startswith(('http://', 'https://')):
                        # 转换路径中的斜杠
                        user_input = user_input.replace('/', '\\')
                        
                        # 检查文件是否存在
                        if not os.path.exists(user_input):
                            print(f"警告: 文件不存在: {user_input}")
                            continue
                    
                    media_paths.append(user_input)
                
                if not media_paths:
                    print("未输入任何有效文件路径或URL，程序退出")
                    sys.exit(1)
            
            # 处理所有输入
            print("\n开始处理...")
            for path in media_paths:
                try:
                    # 验证文件存在性
                    if not path.startswith(('http://', 'https://')) and not os.path.exists(path):
                        raise ValueError(f"文件不存在: {path}")
                    
                    # 处理文件并重命名（清理违规字符）
                    path, was_renamed, original_filename = extract_and_rename_file(path)
                    
                    # 创建一个更简单的临时文件名（用于处理）
                    base_name, ext = os.path.splitext(os.path.basename(path))
                    simple_name = f"temp_{time.time_ns()}"
                    simple_filename = f"{simple_name}{ext}"
                    simple_file_path = os.path.join(os.path.dirname(path), simple_filename)
                    
                    # 重命名文件为简单名称
                    os.rename(path, simple_file_path)
                    print(f"📛 临时重命名: {os.path.basename(path)} → {simple_filename}")
                    
                    # 生成并执行Docker命令
                    command = generate_docker_command(simple_file_path, gpu_base_template, cpu_base_template, use_gpu == 'y')
                    if command:
                        print(f"生成命令: {command}")
                        print(f"📌 原始文件路径: {path}")
                        print(f"📌 简化后文件路径: {simple_file_path}")
                        print(f"📌 Docker命令: {command}")
                        
                        # 执行Docker命令并等待容器完全退出
                        if execute_docker_command(command):
                            print(f"✔ 执行成功: {simple_file_path}")
                            
                            # 等待所有容器退出
                            wait_for_all_containers_to_exit()
                            
                            # 查找所有生成的文件
                            generated_files = find_generated_files(simple_name, media_dir)
                            print(f"🔍 找到 {len(generated_files)} 个生成的文件")
                            
                            # 创建目标目录
                            dest_dir = create_destination_directory(media_dir, original_filename)
                            
                            # 移动生成的文件到目标目录，并恢复原始文件名
                            move_generated_files(generated_files, dest_dir, original_filename, simple_filename)
                            
                            # 将原始文件移动到目标目录
                            if os.path.exists(simple_file_path):
                                # 恢复原始文件名
                                final_file_path = os.path.join(os.path.dirname(simple_file_path), original_filename)
                                os.rename(simple_file_path, final_file_path)
                                
                                # 移动到目标目录
                                shutil.move(final_file_path, os.path.join(dest_dir, original_filename))
                                print(f"✓ 移动原始文件: {original_filename}")
                            else:
                                print(f"✘ 原始文件 {simple_file_path} 不存在，可能已被移动或删除")
                        else:
                            print(f"✘ 执行失败: {simple_file_path}")
                    else:
                        print(f"✘ 命令生成失败: {path}")
                except Exception as e:
                    print(f"✘ 处理出错: {str(e)}")
                    logger.error(f"处理出错: {str(e)}")
    
    except Exception as e:
        logger.error(f"程序错误: {str(e)}")
        print(f"\n✘ 程序错误: {str(e)}")
    finally:
        print("\n程序已完成。按Enter键退出...")
        input()  # 确保程序不会自动关闭

def wait_for_all_containers_to_exit(max_retries=10, retry_interval=2):
    """等待所有Docker容器完全退出（独立函数版本）"""
    print("🕒 检查是否有运行中的Docker容器...")
    
    for attempt in range(max_retries):
        try:
            # 检查是否有运行中的容器
            result = subprocess.run(
                "docker ps -q",
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            if result.returncode != 0:
                print(f"✘ 检查容器时出错: {result.stderr}")
                break
            
            container_ids = result.stdout.strip().split()
            
            if not container_ids:
                print("✔ 没有检测到运行中的Docker容器")
                return True
            
            print(f"检测到 {len(container_ids)} 个运行中的容器，等待 {retry_interval} 秒...")
            time.sleep(retry_interval)
            
        except Exception as e:
            print(f"✘ 检查容器时发生异常: {str(e)}")
            break
    
    print("⚠ 已达到最大重试次数，继续处理文件移动操作")
    return False

if __name__ == "__main__":
    main()    