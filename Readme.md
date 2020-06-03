# FreeCAD scripts for PCB CAD/CAM & FEM

fcad_pcb is yet another way to improve ECAD/MCAD collaboration betweeen
[FreeCAD](http://www.freecadweb.org/) and [KiCAD](http://kicad-pcb.org/). 

The original purpose of these tools was to do **PCB milling in FreeCAD**. It can do much more now. 
It can **generate gcode from kicad_pcb** directly without going though gerber stage. 
It can let your **modify the PCB directly inside FC** (done already), and potentially export back to kicad_pcb (partially done). 
And finally it can **generate solid tracks, pads and plated drills to enable FEM and thermal analysis** on KiCad pcb boards.

## Installation

**fcad_pcb** is written in Python, and requires **FreeCAD 0.17** or later to work properly.

Clone this repo into your freecad macro directory. 
After clone, cd to fcad_pcb, and checkout the submodules

```python
git submodule update --init --recursive
```
## Usage
Start FreeCAD, in the console, the simplest usage:

- generating copper layers

```python
from fcad_pcb import kicad
pcb = kicad.KicadFcad(<full_path_to_your_kicad_pcb_file>)
pcb.makeCoppers()
```

- generating copper layers, pads, drills as a full solid object, ready to be handled in FEM WB

```python
from fcad_pcb import kicad
pcb = kicad.KicadFcad(<full_path_to_your_kicad_pcb_file>)
pcb.make(copper_thickness=0.035, board_thickness=1.53, combo=False, fuseCoppers=True )
```

* supply copper thickness per layer, pass a `dictionary` instead. Use either
  integer (0~31, 0 being the front and 31 the back), or layer name for key. Key
  `None` can be used for default thickness.
```python
pcb.make(copper_thickness={None:0.05, 0:0.04, 'B.Cu':0.09}, board_thickness=1.53, combo=False, fuseCoppers=True)
```


- generating single copper layer

```python
pcb.setLayer('F.Cu')
pcb.makeCopper()
```

* <a name="net-filter"/>filtering by net name (for local nets you have to specify full hirarchical name)

```python
pcb.setNetFilter('GND')
pcb.makeCopper()

pcb.setNetFilter('GND','VCC')
pcb.makeCopper()
```

In case you only want the shape without any intermediate document objects,

```python
from fcad_pcb import kicad
pcb = kicad.KicadFcad(<full_path_to_your_kicad_pcb_file>, add_feature=False)
# Or, you can set the parameter later
pcb.add_feature = False

# All the above makeXXX() calls now returns a shape without creating any features
# For example, if you want the complete fused copper layers. Note 'thickness' can
# be a dictionary for per layer thickness
coppers = pcb.makeCoppers(shape_type='solid',holes=True,fuse=True,thickness=0.05)
Part.show(coppers)
```

Note that there is a **sample board** to play with inside the repo:
[test.kicad_pcb](kicad_parser/test.kicad_pcb)

## Screenshots
**FEM of tracks and drills:**
![Full Board Loaded in FC for FEM](screenshots/solid-tracks-pads-drills-for-FEM.png?raw=true "Full Board Loaded in FC for FEM")

**Full PCB in FreeCAD:**
![Full PCB in FreeCAD](screenshots/full-board-and-tracks.png?raw=true "Full PCB in FreeCAD")

**PCB for milling:**
![PCB for milling](screenshots/pcb-milling.png?raw=true "PCB for milling")

**FEM of tracks and drills:**
![Full Board Loaded in FC for FEM](screenshots/fcad_pcb-generating-for-FEM.gif?raw=true "Full Board Loaded in FC for FEM")

### Requirements
- FreeCAD 0.17, FreeCAD 0.18, FreeCAD 0.19
