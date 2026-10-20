"""S3用の六面体メッシュとケース。既存gsis_toolsだけを使用する。"""
from pathlib import Path
import math
import re
import numpy as np
from gsis_tools.geometry.box import box_mesh
from gsis_tools.geometry.tube import TubeDims, TubeHexOptions, generate_tube_hex
from gsis_tools.pmesh import write_pmesh
HERE=Path(__file__).resolve().parent
GAS='''[gas]
molecular_mass=6.65e-27
mu_ref=1.916e-5
T_ref=273.15
omega=0.66
'''
def slab(kind,delta,alpha=0.5,nx=64,angle=0.0,nv=30):
    tag=f"{kind}_d{delta:g}_a{alpha:g}_n{nx}_r{angle:g}_v{nv}".replace('.','p')
    mesh=box_mesh(np.linspace(0,1,nx+1),[0,1/nx],[0,1/nx],{'left':['xmin'],'right':['xmax'],'side':['ymin','ymax','zmin','zmax']})
    co,si=math.cos(angle),math.sin(angle)
    mesh.points=mesh.points @ np.array([[co,si,0],[-si,co,0],[0,0,1]])
    mesh_name=f"slab_n{nx}_r{angle:g}.pmesh"
    if not (HERE/mesh_name).exists(): write_pmesh(HERE/mesh_name,mesh)
    text=f'[case]\nname="{tag}"\nmesh="{mesh_name}"\nmode="gsis"\n'+GAS
    text+=f'[conditions]\nL_ref=1.0\nT0=273.15\nKn={math.sqrt(math.pi)/(2*delta):.17g}\n'
    speed=math.sqrt(1.380649e-23/6.65e-27*273.15)
    for name,sign in [('left',-1),('right',1)]:
        T=1+sign*0.05 if kind=='heat' else 1
        uy=sign*0.1*speed if kind=='couette' else 0
        text+=f'[[boundary]]\npatch="{name}"\ntype="maxwell_wall"\naccommodation={alpha}\ntemperature={273.15*T:.17g}\nvelocity=[{-si*uy:.17g},{co*uy:.17g},0]\n'
    text+='[[boundary]]\npatch="side"\ntype="periodic_slab"\n'
    if nv==30 and angle==0: text+='[velocity_grid]\nn=[30,24,24]\nrange=[[-10,10],[-8,8],[-8,8]]\n'
    else: text+=f'[velocity_grid]\nn=[{nv},{nv},{nv}]\nrange=[[-8,8],[-8,8],[-8,8]]\n'
    text+='''[kinetic]
backend="cuda"
precision="fp64"
sweep_blocks=1
cell_ordering="none"
sweep_order="block"
inner_sweeps=1
block_passes=1
equilibrium="discrete"
positivity_limiter=true
[gsis]
n_cis=5
max_pbc=10
cfl_pbc=100
tolerance=1e-8
max_iterations=1500
filter_length=1.0
[macro]
init_iterations=0
'''
    (HERE/(tag+'.toml')).write_text(text,encoding='utf-8')
def tube():
    mesh,info=generate_tube_hex(TubeDims(),TubeHexOptions(n_theta=4,ds_wall=0.25,ds_mouth=0.25,h_tube=1.0,h_max=2.0,growth=1.4),renumber=None)
    write_pmesh(HERE/'tube_small.pmesh',mesh)
    print('tube cells',mesh.n_cells)
    base=Path('cases/tube_vacuum/phase5c/tube_d10_p5c.toml').read_text(encoding='utf-8')
    base=base.replace('../mesh/tube_hex.pmesh','tube_small.pmesh').replace('sweep_blocks = 64','sweep_blocks = 4').replace('1.0e-5','1.0e-8')
    base=base.replace('[kinetic]','[kinetic]\nprecision="fp64"')
    base='# S3: 3168セルの六面体管、delta=10。生成条件は本スクリプト。\n'+base[base.index('[case]'):]
    (HERE/'tube_d10_pressure.toml').write_text(base,encoding='utf-8')
    short,_=generate_tube_hex(TubeDims(r_up=1.5,l_up=1,l_tube=2,r_down=1.5,l_down=1),TubeHexOptions(n_theta=4,ds_wall=0.25,ds_mouth=0.25,h_tube=0.5,h_max=1,growth=1.4),renumber=None)
    write_pmesh(HERE/'tube_short.pmesh',short)
    short_case=base.replace('tube_small.pmesh','tube_short.pmesh').replace('sweep_blocks = 4','sweep_blocks = 1').replace('gsis_freeze_kn = 1.0','gsis_freeze_kn = 0.0')
    short_case=re.sub(r'\[output\][\s\S]*?(?=\[kinetic\])','',short_case)
    short_case=short_case.replace('n = [30, 24, 24]','n = [16, 16, 16]').replace('range = [[-10.0, 10.0], [-8.0, 8.0], [-8.0, 8.0]]','range = [[-6.0, 6.0], [-6.0, 6.0], [-6.0, 6.0]]')
    short_case='# S3: 960セルの短い六面体管、delta=10。局所凍結なし。\n'+short_case[short_case.index('[case]'):]
    (HERE/'tube_short_pressure.toml').write_text(short_case,encoding='utf-8')
if __name__=='__main__':
    HERE.mkdir(parents=True,exist_ok=True)
    for kind in ('heat','couette'):
        for delta in (0.1,1.0,10.0):
            for nx in (64,128): slab(kind,delta,nx=nx)
        for angle in (0.0,math.pi/6):
            for nv in (40,64): slab(kind,1.0,nx=128,angle=angle,nv=nv)
    tube()
