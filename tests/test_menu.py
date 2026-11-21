"""Linux root PTY tests against isolated scripts; never touch host service paths."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'posix' and hasattr(os,'geteuid') and os.geteuid()==0 and shutil.which('bash'),
                     'requires Linux root and Bash; run in isolated CI')
class MenuTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.source=self.root/'source'
        shutil.copytree(ROOT,self.source,ignore=shutil.ignore_patterns('.git','__pycache__'))
        self.bin=self.root/'bin'
        self.bin.mkdir()
        self.log=self.root/'actions'
        self.state=self.root/'state'
        self.state.mkdir()
        (self.state/'state.json').write_text('{}')
        unit=self.root/'units'
        unit.mkdir()
        (unit/'xray.service').touch()
        s=(self.source/'menu.sh').read_text()
        s=s.replace('/etc/vps-dr',str(self.state)).replace('/etc/systemd/system',str(unit)).replace('/run/lock',str(self.root))
        (self.source/'menu.sh').write_text(s)
        for name in ('install.sh','preflight.sh','health-check.sh','export-client.sh'):
            self.script(self.source/name,f'printf "%s\\n" "{name} $*" >> "$ACTION_LOG"\nexit "${{STUB_STATUS:-0}}"\n')
        (self.source/'scripts/bbr.py').write_text("import os,sys\nfrom pathlib import Path\nif sys.argv[1] == 'status':\n print(os.environ.get('TEST_BBR_STATUS','未开启（cubic）'))\nelse:\n with Path(os.environ['ACTION_LOG']).open('a') as f: print('bbr enable',file=f)\n sys.exit(int(os.environ.get('BBR_EXIT','0')))\n")
        self.script(self.bin/'systemctl','echo "systemctl $*" >> "$ACTION_LOG"\nexit 0\n')
        self.script(self.bin/'qrencode','exit 0\n')
        self.script(self.bin/'apt-get','exit 1\n')
        self.script(self.bin/'python3',f'if [[ "$2" == public-ip ]]; then\n [[ -n "${{TEST_IP:-}}" ]] && echo "$TEST_IP" && exit 0\n exit 1\nfi\nexec "{sys.executable}" "$@"\n')
        self.env=dict(os.environ,PATH=str(self.bin)+os.pathsep+os.environ['PATH'],ACTION_LOG=str(self.log),TEST_IP='8.8.4.4',SCRIPT=str(self.source/'menu.sh'))
        self.env.pop('VPS_DR_SERVER',None)
        self.env.pop('VPS_DR_DOMAIN',None)

    def script(self,path,body):
        path.write_text('#!/usr/bin/env bash\n'+body)
        path.chmod(0o755)

    def menu(self,inputs,stdout_file=None):
        import pty, select, termios
        pid,fd=pty.fork()
        if pid==0:
            # bootstrap runs menu.sh as a file after consuming piped stdin.
            if stdout_file is not None:
                os.dup2(stdout_file.fileno(),1)
            os.execve('/bin/bash',['bash',str(self.source/'menu.sh')],self.env)
        output=b''
        try:
            settings=termios.tcgetattr(fd)
            settings[3] &= ~termios.ECHO
            termios.tcsetattr(fd,termios.TCSANOW,settings)
            os.write(fd,inputs.encode())
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                ready,_,_=select.select([fd],[],[],0.2)
                if ready:
                    try: chunk=os.read(fd,65536)
                    except OSError: break
                    if not chunk: break
                    output+=chunk
            else:
                os.kill(pid,9)
                self.fail('menu hung')
        finally:
            os.close(fd)
            _,status=os.waitpid(pid,0)
        return os.waitstatus_to_exitcode(status),output.decode(errors='replace')

    def test_terminal_styles_and_plain_output(self):
        self.env['TERM']='xterm-256color'
        self.env.pop('NO_COLOR',None)
        code,styled=self.menu('0\n')
        self.assertEqual(code,0,styled)
        sgr=re.compile(r'\x1b\[([0-9;]*)m')
        attributes={part for params in sgr.findall(styled) for part in params.split(';')}
        self.assertIn('1',attributes)  # Bold text
        self.assertTrue({'36','96'} & attributes)  # Cyan menu accents
        self.assertIn('0',attributes)  # Reset before returning to the terminal
        plain=sgr.sub('',styled)
        self.assertIn('检查运行状态',plain)
        for term,no_color in (('xterm-256color','1'),('dumb',''),('','')):
            with self.subTest(term=term,no_color=no_color):
                self.env.update(TERM=term,NO_COLOR=no_color)
                code,output=self.menu('0\n')
                self.assertEqual(code,0,output)
                self.assertNotIn('\x1b',output)
                self.assertEqual(output,plain)

        self.env.update(TERM='xterm-256color',NO_COLOR='')
        with tempfile.TemporaryFile() as redirected:
            code,terminal=self.menu('0\n',stdout_file=redirected)
            self.assertEqual(code,0,terminal)
            redirected.seek(0)
            output=redirected.read().decode()
        self.assertIn('检查运行状态',output)
        self.assertNotIn('\x1b',output)

    def test_panel_alignment_and_live_service_status(self):
        import unicodedata
        self.env.update(TERM='xterm-256color',NO_COLOR='1')
        for installed,service_code,expected in ((True,0,'服务运行中'),(True,3,'服务未运行'),(False,0,'未安装')):
            with self.subTest(installed=installed,service_code=service_code):
                state_file=self.state/'state.json'
                if installed:
                    state_file.write_text('{}')
                else:
                    state_file.unlink()
                self.script(self.bin/'systemctl',f'exit {service_code}\n')
                code,output=self.menu('0\n')
                self.assertEqual(code,0,output)
                self.assertIn(expected,output)
                lines=[line for line in output.splitlines() if line.startswith('  │')]
                self.assertGreater(len(lines),6)
                for line in lines:
                    width=sum(2 if unicodedata.east_asian_width(c) in ('W','F') else 1 for c in line)
                    self.assertEqual(width,52,line)

    def test_menu_all_branches_and_invalid_choice(self):
        code,output=self.menu('1\n2\n3\n4\n5\n6\n9\n0\n')
        self.assertEqual(code,2)
        log=self.log.read_text()
        for action in ('install.sh ','export-client.sh --server 8.8.4.4 --qr','health-check.sh','systemctl restart xray.service','install.sh --upgrade-management','bbr enable'):
            self.assertIn(action,log)
        self.assertIn('请输入 0、1、2、3、4、5 或 6',output)

    def test_bbr_display_and_failed_enable_return_to_menu(self):
        for state in ('已开启', '未开启（cubic）', '状态不可用'):
            with self.subTest(state=state):
                self.env['TEST_BBR_STATUS'] = state
                self.env['BBR_EXIT'] = '8'
                code, output = self.menu('6\n0\n')
                self.assertEqual(code, 8, output)
                self.assertIn('BBR 状态  ' + state, output)
                self.assertGreaterEqual(output.count('BBR 状态'), 2)
                self.assertIn('退出码 8', output)

    def test_manual_address_and_qr_install_failure(self):
        self.env['TEST_IP']=''
        (self.bin/'qrencode').unlink()
        # Do not depend on whether the runner happens to have qrencode installed.
        for command in ('bash','dirname'):
            (self.bin/command).symlink_to(shutil.which(command))
        self.env['PATH']=str(self.bin)
        code,output=self.menu('2\n1.2.3.4\n0\n')
        self.assertEqual(code,0,output)
        self.assertIn('QR_TOOL_INSTALL_FAILED',output)
        self.assertIn('export-client.sh --server 1.2.3.4 --qr',self.log.read_text())

    def test_invalid_address_is_not_exported(self):
        self.env['TEST_IP']=''
        code,output=self.menu('2\ninvalid address\n0\n')
        self.assertNotEqual(code,0)
        self.assertIn('SERVER_INVALID',output)
        self.assertNotIn("export-client.sh", self.log.read_text() if self.log.exists() else "")

    def test_failed_operation_returns_to_menu_and_exit_status(self):
        self.env['STUB_STATUS']='7'
        code,output=self.menu('1\n0\n')
        self.assertEqual(code,7,output)
        self.assertGreaterEqual(output.count('检查运行状态'),2)

    def test_no_tty_fails_without_hanging(self):
        result=subprocess.run(['/bin/bash',str(self.source/'menu.sh')],stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=self.env,
                              start_new_session=True,timeout=5,text=True)
        self.assertEqual(result.returncode,1)
        self.assertIn('TTY_REQUIRED',result.stderr)

    def test_bootstrap_from_stdin_selects_new_main_or_fixed_release(self):
        import tarfile
        package=self.root/'package'
        package.mkdir()
        (package/'VERSION').write_text('1.1.0\n')
        self.script(package/'menu.sh','echo MENU_ENTRY_PASS\n')
        self.script(package/'install.sh','echo INSTALL_ENTRY_PASS\n')
        archive=self.root/'package.tar.gz'
        with tarfile.open(archive,'w:gz') as bundle:
            bundle.add(package,arcname='vps-dr-test')
        self.script(self.bin/'curl','echo "$*" >> "$ACTION_LOG"\nwhile (( $# )); do\n if [[ "$1" == -o ]]; then cp "$TEST_ARCHIVE" "$2"; exit; fi\n shift\ndone\nexit 1\n')
        self.env['TEST_ARCHIVE']=str(archive)
        for ref,argument,expected in (('main',None,'MENU_ENTRY_PASS'),('v1.1.0','--install','INSTALL_ENTRY_PASS')):
            self.env['VPS_DR_REF']=ref
            command=['/bin/bash'] if argument is None else ['/bin/bash','-s','--',argument]
            result=subprocess.run(command,input=(ROOT/'bootstrap.sh').read_text(),stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE,env=self.env,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn(expected,result.stdout)
        log=self.log.read_text()
        self.assertIn('refs/heads/main.tar.gz',log)
        self.assertIn('refs/tags/v1.1.0.tar.gz',log)
