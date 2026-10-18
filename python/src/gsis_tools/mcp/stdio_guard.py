"""stdio の通信路を、gmsh・OpenCASCADE の標準出力への書き込みから守る。

stdio の MCP では標準出力が JSON-RPC の通信路になる。gmsh(OpenCASCADE)は ``General.Terminal = 0``
にしても、STEP の書き出しなどで C の標準出力に統計を直接書く。これが通信路に混ざると、
クライアントが応答を読めなくなる。

Windows の gmsh(``gmsh-4.15.dll``)は MinGW で作られ、システムの ``msvcrt.dll`` の標準出力を使う。
msvcrt.dll はプロセスの起動時に読み込まれて標準ハンドルを覚えるので、Python(UCRT)の fd 1 を
付け替えても(MCP SDK の ``stdio_server()`` が行う処理)gmsh の出力は止まらない。そこで:

1. 通信路として fd 0・1 の複製(新しいハンドル)を先に作る
2. msvcrt の fd 1 を標準エラーに、fd 0 を NUL に付け替える(MCP SDK と同じく、標準入力を読む
   処理が通信路の読み取りと競合しないようにする。gmsh の DLL は読み込み時に標準入力を調べ、
   通信路の読み取りが終わるまで待たされることがあった)
3. UCRT の fd 0・1 と Win32 の標準ハンドルも同じ先に合わせる
4. ``sys.stdout`` を ``sys.stderr`` に向ける

2 と 3 では、2 つの CRT が同じハンドルの値を持っているので、片方が閉じたハンドルの値が
もう片方に残らないように、閉じた後のハンドルの値を比べて揃える。

Windows 以外では libc が 1 つなので、MCP SDK の ``stdio_server()`` の既定の処理(fd の付け替え)で足りる。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass


@dataclass
class WireFds:
    """通信路の fd(UCRT の fd。Windows 以外では None = SDK の既定に任せる)。"""

    stdin_fd: int | None
    stdout_fd: int | None


def _guard_windows() -> WireFds:
    import ctypes
    import msvcrt as ucrt  # Python の msvcrt モジュールは UCRT の関数

    wire_out = os.dup(1)
    wire_in = os.dup(0)
    try:
        crt = ctypes.CDLL("msvcrt")
    except OSError:  # pragma: no cover - msvcrt.dll がない環境
        crt = None
    if crt is not None:
        crt._get_osfhandle.restype = ctypes.c_ssize_t
        # 標準出力: msvcrt の fd 1 を標準エラーの複製に(標準エラーがなければ NUL に)
        if crt._dup2(2, 1) != 0:
            k = crt._open(b"NUL", 1)  # _O_WRONLY
            if k >= 0:
                crt._dup2(k, 1)
                crt._close(k)
        if ucrt.get_osfhandle(1) != crt._get_osfhandle(1):
            try:
                os.dup2(2, 1)
            except OSError:
                nul = os.open(os.devnull, os.O_WRONLY)
                os.dup2(nul, 1)
                os.close(nul)
        # 標準入力: msvcrt の fd 0 を NUL に
        k = crt._open(b"NUL", 0)
        if k >= 0:
            crt._dup2(k, 0)
            crt._close(k)
        if ucrt.get_osfhandle(0) != crt._get_osfhandle(0):
            nul = os.open(os.devnull, os.O_RDONLY)
            os.dup2(nul, 0)
            os.close(nul)
    else:  # pragma: no cover
        os.dup2(2, 1)
        nul = os.open(os.devnull, os.O_RDONLY)
        os.dup2(nul, 0)
        os.close(nul)
    k32 = ctypes.windll.kernel32
    k32.SetStdHandle.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    k32.SetStdHandle.restype = ctypes.c_int
    k32.SetStdHandle(ctypes.c_uint32(-11 & 0xFFFFFFFF), ucrt.get_osfhandle(1))  # STD_OUTPUT_HANDLE
    k32.SetStdHandle(ctypes.c_uint32(-10 & 0xFFFFFFFF), ucrt.get_osfhandle(0))  # STD_INPUT_HANDLE
    return WireFds(wire_in, wire_out)


def guard_stdio() -> WireFds:
    """標準入出力を通信路から切り離す(プロセスで 1 回、gmsh を読み込む前に呼ぶ)。

    Windows 以外では何もしない(SDK の ``stdio_server()`` が ``sys.stdin``・``sys.stdout`` の fd を
    付け替えるので、ここで ``sys.stdout`` などを差し替えてはいけない)。
    """
    if os.name != "nt":
        return WireFds(None, None)
    fds = _guard_windows()
    sys.stdout = sys.stderr
    try:
        sys.stdin = open(os.devnull, "r", encoding="utf-8")
    except OSError:  # pragma: no cover
        pass
    return fds


def wire_streams(fds: WireFds):
    """通信路の fd から、MCP SDK の ``stdio_server(stdin=, stdout=)`` に渡すストリームを作る。

    Windows 以外(fd が None)では (None, None) を返し、SDK の既定に任せる。
    """
    if fds.stdin_fd is None or fds.stdout_fd is None:
        return None, None
    from io import TextIOWrapper

    import anyio

    rd = TextIOWrapper(open(fds.stdin_fd, "rb", closefd=False), encoding="utf-8", errors="replace")
    # 改行は LF だけにする(1 行 1 メッセージ)
    wr = TextIOWrapper(open(fds.stdout_fd, "wb", closefd=False), encoding="utf-8", newline="\n")
    return anyio.wrap_file(rd), anyio.wrap_file(wr)
