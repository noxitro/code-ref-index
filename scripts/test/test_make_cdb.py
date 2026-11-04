"""make-cdb.ps1 と index-project.ps1 のテスト。一時フォルダに Visual Studio と CMake の小さなプロジェクトを作って通す。
Windows と、C++ のツールを入れた Visual Studio が要る(無ければ飛ばす)。index-project は ltidx.exe も要る。

usage: python -I -m unittest discover -s scripts/test   (リポジトリのルートで)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, '..')
LTIDX = os.path.join(HERE, '..', '..', 'indexer', 'build', 'ltidx.exe')
VSWHERE = os.path.join(os.environ.get('ProgramFiles(x86)', ''), 'Microsoft Visual Studio', 'Installer', 'vswhere.exe')
PWSH = shutil.which('pwsh')

VCXPROJ = '''<?xml version="1.0" encoding="utf-8"?>
<Project DefaultTargets="Build" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <ItemGroup Label="ProjectConfigurations">
    <ProjectConfiguration Include="Debug|x64"><Configuration>Debug</Configuration><Platform>x64</Platform></ProjectConfiguration>
    <ProjectConfiguration Include="Release|x64"><Configuration>Release</Configuration><Platform>x64</Platform></ProjectConfiguration>
  </ItemGroup>
  <PropertyGroup Label="Globals"><ProjectGuid>{6A0D3E1C-2B4F-4C8A-9E5D-1F2A3B4C5D6E}</ProjectGuid><Keyword>Win32Proj</Keyword></PropertyGroup>
  <Import Project="$(VCTargetsPath)\\Microsoft.Cpp.Default.props" />
  <PropertyGroup><ConfigurationType>Application</ConfigurationType><CharacterSet>Unicode</CharacterSet></PropertyGroup>
  <Import Project="$(VCTargetsPath)\\Microsoft.Cpp.props" />
  <PropertyGroup><IncludePath>$(ProjectDir)vcdir;$(IncludePath)</IncludePath></PropertyGroup>
  <ItemDefinitionGroup>
    <ClCompile>
      <AdditionalIncludeDirectories>inc;%(AdditionalIncludeDirectories)</AdditionalIncludeDirectories>
      <PreprocessorDefinitions Condition="'$(Configuration)'=='Debug'">ONLY_DEBUG;%(PreprocessorDefinitions)</PreprocessorDefinitions>
      <LanguageStandard>stdcpp20</LanguageStandard>
    </ClCompile>
  </ItemDefinitionGroup>
  <ItemGroup><ClCompile Include="a.cpp" /><ClCompile Include="b.cpp" /></ItemGroup>
  <Import Project="$(VCTargetsPath)\\Microsoft.Cpp.targets" />
</Project>
'''
A_CPP = '#include "h.h"\n#include "v.h"\nint main() {\n#ifdef ONLY_DEBUG\n  Debug();\n#endif\n  return F() + V();\n}\n'


def has_vs():
    if os.name != 'nt' or not PWSH or not os.path.exists(VSWHERE):
        return False
    r = subprocess.run([VSWHERE, '-latest', '-products', '*', '-requires',
                        'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-property', 'installationPath'],
                       capture_output=True, text=True)
    return bool(r.stdout.strip())


@unittest.skipUnless(has_vs(), 'Windows, pwsh and Visual Studio with the C++ tools are required')
class MakeCdbTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='cri')  # MSBuild の中間ファイルが 260 文字を超えないよう短く
        self.write('inc/h.h', 'inline int F() { return 1; }\n')
        self.write('vcdir/v.h', 'inline int V() { return 2; }\n')  # 「VC++ ディレクトリ」(IncludePath)だけにある
        self.write('a.cpp', 'void Debug();\n' + A_CPP)
        self.write('b.cpp', '#include "h.h"\nint G() { return F(); }\n')

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, rel, text):
        fp = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)

    def ps(self, script, *args):
        r = subprocess.run([PWSH, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', os.path.join(SCRIPTS, script)]
                           + list(args), capture_output=True, text=True, encoding='utf-8', errors='replace')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def cdb(self, out):
        with open(os.path.join(out, 'compile_commands.json'), encoding='utf-8-sig') as f:
            return {os.path.basename(e['file']): e for e in json.load(f)}

    def check_vs(self, project, cfg='Debug'):
        out = os.path.join(self.root, 'out-' + cfg)
        self.ps('make-cdb.ps1', '-Root', self.root, '-Project', os.path.join(self.root, project),
                '-Configuration', cfg, '-Out', out)
        c = self.cdb(out)
        self.assertEqual(set(c), {'a.cpp', 'b.cpp'})
        cmd = c['a.cpp']['command']
        self.assertIn('/Iinc', cmd)
        self.assertIn('/std:c++20', cmd)
        self.assertIn('/D UNICODE', cmd)
        self.assertIn('vcdir', cmd)  # IncludePath は /imsvc で足す
        self.assertEqual('/D ONLY_DEBUG' in cmd, cfg == 'Debug')
        return out

    def test_vcxproj(self):
        self.write('p.vcxproj', VCXPROJ)
        self.check_vs('p.vcxproj')
        self.check_vs('p.vcxproj', 'Release')

    def test_slnx(self):
        self.write('p.vcxproj', VCXPROJ)
        self.write('p.slnx', '<Solution>\n  <Project Path="p.vcxproj" />\n</Solution>\n')
        self.check_vs('p.slnx')

    def test_sln_maps_configuration(self):
        # ソリューションの Debug|x64 を、プロジェクトの Release|x64 に対応づけた .sln
        self.write('p.vcxproj', VCXPROJ)
        g = '{6A0D3E1C-2B4F-4C8A-9E5D-1F2A3B4C5D6E}'
        self.write('p.sln', 'Microsoft Visual Studio Solution File, Format Version 12.00\n'
                   f'Project("{{8BC9CEB8-8B4A-11D0-8D11-00A0C91BC942}}") = "p", "p.vcxproj", "{g}"\nEndProject\n'
                   'Global\n\tGlobalSection(ProjectConfigurationPlatforms) = postSolution\n'
                   f'\t\t{g}.Debug|x64.ActiveCfg = Release|x64\n\tEndGlobalSection\nEndGlobal\n')
        out = os.path.join(self.root, 'out-sln')
        self.ps('make-cdb.ps1', '-Root', self.root, '-Out', out)  # -Project を省くとルートの .sln を探す
        self.assertNotIn('ONLY_DEBUG', self.cdb(out)['a.cpp']['command'])

    def test_cmake(self):
        self.write('CMakeLists.txt', 'cmake_minimum_required(VERSION 3.20)\nproject(p CXX)\n'
                   'add_executable(p a.cpp b.cpp)\ntarget_include_directories(p PRIVATE inc vcdir)\n'
                   'target_compile_definitions(p PRIVATE $<$<CONFIG:Debug>:ONLY_DEBUG>)\n')
        out = os.path.join(self.root, 'out-cmake')
        self.ps('make-cdb.ps1', '-Root', self.root, '-Out', out)
        c = self.cdb(out)
        self.assertEqual(set(c), {'a.cpp', 'b.cpp'})
        self.assertIn('ONLY_DEBUG', c['a.cpp']['command'])

    @unittest.skipUnless(os.path.exists(LTIDX), 'ltidx.exe is not built')
    def test_index_project_makes_cdb(self):
        # compile_commands.json が無いルートでも、index-project だけで索引ができる
        self.write('p.vcxproj', VCXPROJ)
        log = self.ps('index-project.ps1', '-Root', self.root, '-Jobs', '2')
        d = os.path.join(self.root, '.index')
        with open(os.path.join(d, 'index.tsv.tu.tsv'), encoding='utf-8') as f:
            tus = f.read()
        with open(os.path.join(d, 'index.tsv'), encoding='utf-8') as f:
            text = f.read()
        with open(os.path.join(d, 'cdb-Debug-x64', 'compile_commands.json'), encoding='utf-8-sig') as f:
            cdb = f.read()
        info = f'\nroot={self.root}\n{log}\n{tus}\n{text[:2000]}\n{cdb[:3000]}'
        self.assertEqual([line.split('\t')[1] for line in tus.splitlines()[1:]], ['1', '1'], info)
        rows = [line.split('\t') for line in text.splitlines()]
        self.assertTrue(any(r[0] == 'V' and r[1] == 'call' for r in rows), info)  # IncludePath のヘッダーも解決できた
        self.assertTrue(any(r[0] == 'Debug' and r[1] == 'call' for r in rows), info)


if __name__ == '__main__':
    unittest.main()
