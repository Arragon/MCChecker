"""Check parameter text containment against a running configuration page (read-only)."""
import sys,json,argparse
sys.path.insert(0,str(__import__('pathlib').Path(__file__).resolve().parent))
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--url', required=True, help='Configuration version evolution URL')
args=parser.parse_args()
from e2e_cdp import Chrome
b=Chrome()
try:
 b.viewport(1366,873)
 b.navigate(args.url)
 assert b.wait_for("!!document.querySelector('.cv32-evo-scroll')",timeout=15)
 b.eval("document.querySelector('.cv-release-band button').click()")
 assert b.wait_for("!!document.querySelector('.cv32-parameter-name')",timeout=10)
 print('REAL PARAMETERS',b.eval("document.querySelector('.cv32-parameter-table').innerText"))
 # Render stress text only in this disposable browser DOM; never change saved business data.
 b.eval("document.querySelector('.cv32-parameter-name').textContent='长参数名称与说明'.repeat(12); document.querySelector('.cv32-parameter-directory').textContent='长目录'.repeat(15)+'/子目录'")
 for w,h in [(1366,768),(768,1024),(375,812)]:
  b.viewport(w,h)
  result=b.eval("""(()=>{let n=document.querySelector('.cv32-parameter-name'),d=document.querySelector('.cv32-parameter-directory'),r=n.closest('td').getBoundingClientRect();return {overlap:n.getBoundingClientRect().bottom>d.getBoundingClientRect().top,inside:d.getBoundingClientRect().bottom<=r.bottom,overflow:document.body.scrollWidth>innerWidth}})()""")
  assert not result['overlap'] and result['inside'] and not result['overflow'],(w,result)
  print('LONG TEXT',w,result)
finally:b.close()
