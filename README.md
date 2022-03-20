## FreeCAD scripts for PCB CAD/CAM & FEM

fcad_pcb is yet another way to improve ECAD/MCAD collaboration between
[FreeCAD](https://www.freecad.org/) and [KiCAD](https://kicad.org/).

The original purpose of these tools was to do **PCB milling in FreeCAD**. It can do much more now.:
* It can **generate gcode from kicad_pcb** directly without going through the gerber stage.
* It can let your **modify the PCB directly inside FC** (done already),
* and potentially export back to kicad_pcb (partially done).
* and finally it can **generate solid tracks, pads and plated drills to enable FEM and thermal analysis** on KiCad pcb boards.

## Installation

The fcad_pcb macro is written in Python and requires **FreeCAD 0.17** or later to work properly.

1. Clone this repo into your freecad macro directory. To check what the default path of your macro directory is go to dropdown `Macro` > `Macros..` and find the path in the field User macros location
    ```bash
    cd <path/to/your/macros/directory>
    git clone https://github.com/realthunder/fcad_pcb/
    ```
2. Enter the locally cloned repository
    ```bash
    cd fcad_pcb/
    ``` 
3. Download the repository submodules
    ```python
    git submodule update --init --recursive
    ```
4. Restart FreeCAD

## Usage

At this time fcad is usable through the [FreeCAD python console](https://wiki.freecad.org/Python_console). 

* Start FreeCAD,
* Launch the python console
  Enable through the `View > Panels > Python Console` dropdown menu
* Invoke the python `import` command to load fcad_pcb:
  ```python
  from fcad_pcb import kicad
  ```
**Result:** you are now ready to use fcad_pcb. 

#### Generating copper layers

```python
from fcad_pcb import kicad
pcb = kicad.KicadFcad(<full_path_to_your_kicad_pcb_file>)
pcb.makeCoppers()
```

**Note:** the file path syntax should be one of the following:
  ```python
  pcb = kicad.KicadFcad('C:/Users/fooDesktop/MyProject/MyPCBfilekicad_pcb')
  ```

  Alternatively if you don't want to replace backslashes for a Windows system:
  
  ```python
  pcb = kicad.KicadFcad(r'C:\Users\foo\Desktop\MyProject\MyPCBfile.kicad_pcb')`
  ```

#### Generating copper layers / pads / drills + ready for FEM workbench

Generate these full solid objects ready to for the FEM workbench

  ```python
  from fcad_pcb import kicad
  pcb = kicad.KicadFcad(<full_path_to_your_kicad_pcb_file>)
  pcb.make(copper_thickness=0.035, board_thickness=1.53, combo=False, fuseCoppers=True )
  #
  # NOTE: KiCAD 5.99 and later added possibility to specify per layer thickness including
  #       dielectric layers. You are no longer required to explicitly supply thickness
  #       parameters in any of the function calls as shown above.
  ```

#### Supply copper thickness per layer, pass a `dictionary` instead.

Use either **`integer`** or **`layer name`**
- **`integer`** (0~31, `0` being the front and `31` the back)
- **`layer name`** for key.
  **Note:** key `None` can be used for default thickness.

  ```python
  pcb.make(copper_thickness={None:0.05, 0:0.04, 'B.Cu':0.09},
           board_thickness=1.53, combo=False, fuseCoppers=True)
  #
  # NOTE: KiCAD 5.99 and later added possibility to specify per layer thickness
  #       including dielectric layers. You are no longer required to explicitly
  #       supply thickness parameters in any of the function calls as shown above.
  ```


#### Generating a single copper layer

```python
pcb.setLayer('F.Cu')
pcb.makeCopper()
```

#### <a name="net-filter"/>Filtering by net name</a>

For local nets you have to specify full hierarchical name

  ```python
  pcb.setNetFilter('GND')
  pcb.makeCopper()

  pcb.setNetFilter('GND','VCC')
  pcb.makeCopper()
  ```

#### Shape without intermediate document objects
In case you only want the shape without any intermediate document objects

  ```python
  from fcad_pcb import kicad
  pcb = kicad.KicadFcad(<full_path_to_your_kicad_pcb_file>, add_feature=False)

  # Or, you can set the parameter later
  pcb.add_feature = False

  # All the above makeXXX() calls now returns a shape without creating any features
  # For example, if you want the complete fused copper layers.
  # Note: 'thickness' can be a dictionary for per layer thickness
  coppers = pcb.makeCoppers(shape_type='solid', holes=True, fuse=True)
  Part.show(coppers)
  ```

  **Note:** that there is a **sample board** to play with inside this repo: [test.kicad_pcb](kicad_parser/test.kicad_pcb)

## Screenshots

#### FEM of tracks and drills
![Full Board Loaded in FC for FEM](screenshots/solid-tracks-pads-drills-for-FEM.png?raw=true "Full Board Loaded in FC for FEM")

#### Full PCB in FreeCAD
![Full PCB in FreeCAD](screenshots/full-board-and-tracks.png?raw=true "Full PCB in FreeCAD")

#### PCB for milling
![PCB for milling](screenshots/pcb-milling.png?raw=true "PCB for milling")

#### FEM of tracks and drills
![Full Board Loaded in FC for FEM](screenshots/fcad_pcb-generating-for-FEM.gif?raw=true "Full Board Loaded in FC for FEM")

### Requirements

`FreeCAD >= v0.17`
