# CAM-JetCutting

![jetcutter_tools.svg](Resources/Icons/jetcutter_tools.svg)

FreeCAD addon that adds two tools to the CAM Workbench toolbar for jet cutting workflow assistance.

![CAMToolBar.png](Resources/ScreenShots/CAMToolBar.png)

In many cases on a Jet CNC tool like Plasma, Laser or Waterjet the user will have a large sheet of parts to be cut. These will include a significant number of internal and externl profiles.

There are two distinct use cases that these tools help with:

[1] **Lots of regular shaped cutouts** where the start position of the cut on the profile is not material.In which case mass selecting all the edges of the same size (e.g. round holes, slots) and including them in a single Profile Operation will be effective.  "Same Edges as Highlighted" will help you in this scenario. 

[2] **Lots of irregular and/or narrow cutouts** where the start position can matter. Due to many narrow and/or irregular holes (e.g. artisitc panels and screens) each "hole" in the sheet needs its own operation so that start positions for that cut can be defined. "Find Profiles" will help you in this use case.

## Installation

Install via the FreeCAD Addon Manager, or place this package in your FreeCAD `Mod/` directory.

## Requirements

- FreeCAD 1.1.0 or later
- CAM Workbench (bundled with FreeCAD)

## Commands

The addon adds a "JetCutter Tools" toolbar to the CAM Workbench with two commands:

### Same Edges As Highlighted

![same-edges-as-highlighted.svg](Resources/Icons/same-edges-as-highlighted.svg)

Finds and selects all edges in the active document that match a template profile defined by highlighted edges. Matching is based on edge length and global Z-level.

**How it works:**

1. Select one or more edges on any visible solid in the document
2. Click the "Same Edges As Highlighted" button
3. The command extracts the length and Z-level from each selected edge
4. It scans all visible solids in the document (excluding groups and parts containers)
5. For each solid, it finds edges that match all template edges within a tolerance of 0.001mm
6. Matching edges are selected, grouped by object

**Use case:** Quickly select matching edges across multiple parts for simultaneous operations, such as finding all edges at the same Z-level for profiling or cutting.

### Find Profiles

![FindProfiles.svg](Resources/Icons/FindProfiles.svg)

Creates CAM Profile operations for internal edges (holes and cutouts) on the top faces of a selected CAM Job. Optionally detects and creates operations for concave indentations in the outer wire.

**How it works:**

1. Select a CAM Job object in the tree
2. Click the "Find Profiles" button
3. A dialog appears to configure the profile operations:
   - **Tool:** Select a ToolController from the Job's Tools folder
   - **Offset Side:** Inside, Outside, or None (no compensation)
   - **Cut Direction:** Clockwise (CW) or Counter-Clockwise (CCW)
   - **LeadInOut Dressup:** Enable/disable with configurable style and length
   - **Concave Detection:** Enable/disable detection of concave indentations
   - **Concave Depth Threshold:** Minimum depth for concave detection (mm)
4. The command identifies top-facing flat planes on the model geometry
5. For each top face, it finds closed internal wires (holes/cutouts)
6. Profile operations are created for each internal loop, linked to the selected edges
7. If concave detection is enabled, chains of edges forming concave indentations in the outer wire are also processed

**Use case:** Automatically generate profile operations for all internal features (holes, pockets, cutouts) on a part's top surface, saving manual selection and operation creation time.

## Development

This addon uses the FreeCAD Workbench Manipulator pattern to contribute commands to the existing CAM Workbench toolbar. See the Addon Academy documentation for details:

- https://freecad.github.io/Addon-Academy/Guides/Code/Manipulators/

For detailed algorithm descriptions and architecture, see [DESIGN.md](DESIGN.md).

## License

LGPL-2.1-or-later (see LICENSE-Code and LICENSE-Assets)
