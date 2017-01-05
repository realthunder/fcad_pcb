from __future__ import (absolute_import, division,
        print_function, unicode_literals)
from builtins import *
from future.utils import iteritems

import logging
from collections import defaultdict
from math import sqrt, atan2, degrees, sin, cos, radians, pi, hypot
import traceback
import FreeCAD
import FreeCADGui
import Part
from FreeCAD import Vector,Placement,Rotation
import DraftGeomUtils
import DraftVecUtils
from kicad_parser import KicadPCB,SexpList

def getActiveDoc():
    if FreeCAD.ActiveDocument is None:
        return FreeCAD.newDocument('kicad_fcad')
    return FreeCAD.ActiveDocument

def isZero(f):
    return round(f,DraftGeomUtils.precision())==0

def findWires(edges):
    return [ Part.Wire(e) for e in Part.sortEdges(edges) ]

def makeVect(l):
    return Vector(l[0],-l[1],0)

def getAt(at):
    v = makeVect(at)
    return (v,0) if len(at)==2 else (v,at[2])

def product(v1,v2):
    return Vector(v1.x*v2.x,v1.y*v2.y,v1.z*v2.z)

def make_rect(size):
    return Part.makePolygon([product(size,Vector(*v))
        for v in ((-0.5,-0.5),(-0.5,0.5),(0.5,0.5),(0.5,-0.5),(-0.5,-0.5))])

def make_circle(size):
    return Part.Wire(Part.makeCircle(size.x*0.5))

def make_oval(size):
    if size.x == size.y:
        return make_circle(size)
    if size.x < size.y:
        r = size.x*0.5
        size.y -= size.x
        s  = ((0,0.5),(-0.5,0.5),(-0.5,-0.5),(0,-0.5),(0.5,-0.5),(0.5,0.5))
        a = (0,180,180,360)
    else:
        r = size.y*0.5
        size.x -= size.y
        s = ((-0.5,0),(-0.5,-0.5),(0.5,-0.5),(0.5,0),(0.5,0.5),(-0.5,0.5))
        a = (90,270,-90,-270)
    pts = [product(size,Vector(*v)) for v in s]
    return Part.Wire([
            Part.makeCircle(r,pts[0],Vector(0,0,1),a[0],a[1]),
            Part.makeLine(pts[1],pts[2]),
            Part.makeCircle(r,pts[3],Vector(0,0,1),a[2],a[3]),
            Part.makeLine(pts[4],pts[5])])

def makeThickLine(p1,p2,width):
    length = p1.distanceToPoint(p2)
    line = make_oval(Vector(length+2*width,2*width))
    p = p2.sub(p1)
    a = -degrees(DraftVecUtils.angle(p))
    line.translate(Vector(length*0.5))
    line.rotate(Vector(),Vector(0,0,1),a)
    line.translate(p1)
    return line

def makeArc(center,start,angle):
    p = start.sub(center)
    r = p.Length
    a = -degrees(DraftVecUtils.angle(p))
    # NOTE: KiCAD pcb geometry runs in clockwise, while FreeCAD is CCW. So the
    # resulting arc below is the reverse of what's specified in kicad_pcb
    arc = Part.makeCircle(r,center,Vector(0,0,1),a-angle,a)
    arc.reverse();
    return arc

def getFaceCompound(shape,wire=False):
    objs = []
    for f in shape.Faces:
        selected = True
        for v in f.Vertexes:
            if not isZero(v.Z):
                selected = False
                break
        if not selected:
            continue

        ################################################################
        ## TODO: FreeCAD curve.normalAt is not implemented
        ################################################################
        # for e in f.Edges:
            # if isinstance(e.Curve,(Part.LineSegment,Part.Line)): continue
            # if not isZero(e.normalAt(Vector()).dot(Vector(0,0,1))):
                # selected = False
                # break
        # if not selected: continue

        if not wire:
            objs.append(f)
            continue
        for w in f.Wires:
            objs.append(w)
    if not objs:
        raise ValueError('null shape')
    return Part.makeCompound(objs)

def makeFace(objs,fuse=False):
    if not objs:
        raise ValueError('null shape')
    shape = Part.makeFace(objs,'Part::FaceMakerBullseye')
    if not fuse:
        return shape
    return shape.makeOffset2D(0,algo=3,intersection=True)

class FCADLogger:
    def isEnabledFor(self,__):
        return True

    def debug(self,msg):
        FreeCAD.Console.PrintLog(msg+'\n')
        FreeCADGui.updateGui()

    def info(self,msg):
        FreeCAD.Console.PrintMessage(msg+'\n')
        FreeCADGui.updateGui()

    def error(self,msg):
        FreeCAD.Console.PrintError(msg+'\n')
        FreeCADGui.updateGui()

    def warning(self,msg):
        FreeCAD.Console.PrintWarning(msg+'\n')
        FreeCADGui.updateGui()


class KicadFcad:
    def __init__(self,filename=None):
        if not filename:
            filename = '/home/thunder/pwr.kicad_pcb'
        self.pcb = KicadPCB.load(filename)
        self.layer = self.pcb.layers['0'][0]
        self.prefix = ''
        self.indent = '  '
        self.logger = FCADLogger()
        #  self.logger = logging.getLogger(__name__)

    def setLayer(self,layer):
        try:
            layer = int(layer)
        except:
            for l in self.pcb.layers:
                if l[0] == layer:
                    self.layer = layer
                    return
            raise KeyError('layer not found')
        else:
            layer = str(layer)
            if layer not in self.pcb.layers:
                raise KeyError('layer not found')
            self.layer = self.pcb.layers[layer][0]

    def _log(self,msg,*arg,**kargs):
        level = 'info'
        if kargs:
            if 'level' in kargs:
                level = kargs['level']
        if not self.logger.isEnabledFor(getattr(logging,level.upper())):
            return
        getattr(self.logger,level)('{}{}'.format(self.prefix,msg.format(*arg)))

    def _pushLog(self,msg=None,*arg,**kargs):
        if msg:
            self._log(msg,*arg,**kargs)
        if 'prefix' in kargs:
            prefix = kargs['prefix']
            if prefix is not None:
                self.prefix = prefix
        self.prefix += self.indent

    def _popLog(self,msg=None,*arg,**kargs):
        self.prefix = self.prefix[:-len(self.indent)]
        if msg:
            self._log(msg,*arg,**kargs)

    def _makeCompound(self,objs,name,shape_type,fuse,add_feature):
        if not objs:
            raise ValueError('null shape')

        self._log('making {} compound...',name)
        if add_feature:
            name = '{}_{}'.format(name,shape_type)
            doc = getActiveDoc()
            if isinstance(objs[0],Part.Shape):
                compound = doc.addObject('Part::Feature',name)
                compound.Shape = Part.makeCompound(objs)
            else:
                compound = doc.addObject('Part::Compound',name)
                compound.Links = objs
                doc.recompute()
            compound.Label = '{}_{}'.format(compound.Name,self.layer)
            shape = compound.Shape
        else:
            compound = shape = Part.makeCompound(objs)

        if not fuse:
            return compound

        face = False
        wire = False
        if not shape.Wires:
            raise ValueError('null shape')
        if not shape.Faces:
            if len(shape.Wires)==1:
                return compound
            shape = Part.makeCompound([Part.Face(w)
                for w in shape.Wires]).extrude(Vector(0,0,1))
            wire = True
        elif not shape.Solids:
            if len(shape.Faces)==1:
                return compound
            shape = shape.extrude(Vector(0,0,1))
            face = True
        elif len(shape.Solids)==1:
            return compound

        self._log('making {} fuse...please wait',name)
        shape = shape.Solids[0].multiFuse(
                        shape.Solids[1:]).removeSplitter()

        if face or wire:
            shape = getFaceCompound(shape,wire)

        if not add_feature:
            return shape

        obj = doc.addObject('Part::Feature',
                    '{}_fuse'.format(compound.Name))
        obj.Label = '{}_{}'.format(obj.Name,self.layer)
        obj.Shape = shape
        compound.ViewObject.Visibility = False
        return (obj,compound)

    def makeBoard(self, shape_type='solid', thickness=0,fuse=False,
            add_feature=True, minHoleSize=0,ovalHole=True,prefix=''):
        edges = []

        self._pushLog('making board...',prefix=prefix)
        self._log('making {} lines',len(self.pcb.gr_line))
        for l in self.pcb.gr_line:
            if l.layer != 'Edge.Cuts':
                continue
            edges.append(Part.makeLine(makeVect(l.start),makeVect(l.end)))

        self._log('making {} arcs',len(self.pcb.gr_arc))
        for l in self.pcb.gr_arc:
            if l.layer != 'Edge.Cuts':
                continue
            # for gr_arc, 'start' is actual the center, and 'end' is the start
            edges.append(makeArc(makeVect(l.start),makeVect(l.end),l.angle))

        wires = findWires(edges)

        if ovalHole or minHoleSize is not None:
            self._pushLog()
            holes = self.makeHoles(shape_type='path', delta=0, fuse=True,
                        add_feature=False, minSize=minHoleSize, oval=True,
                        prefix=None)
            for h in holes:
                wires += h.Wires
            self._popLog()

        if not thickness:
            thickness = self.pcb.general.thickness

        def _path():
            return Part.makeCompound(_face().Wires)

        def _face():
            return makeFace(wires,fuse)

        def _solid():
            return _face().extrude(Vector(0,0,thickness))

        try:
            func = locals()['_{}'.format(shape_type)]
        except KeyError:
            raise ValueError('invalid shape type: {}'.format(shape_type))

        shape = func()
        if not add_feature:
            return shape
        obj = FreeCAD.ActiveDocument.addObject('Part::Feature',
                'board_{}'.format(shape_type))
        obj.Shape = shape
        self._popLog('board done')
        return obj

    def makeHoles(self,shape_type='face',thickness=0,fuse=False,delta=0.001,
            add_feature=True,minSize=0,maxSize=0,oval=False,prefix=''):

        self._pushLog('making holes...',prefix=prefix)

        holes = defaultdict(list)
        ovals = defaultdict(list)

        if not thickness:
            thickness = self.pcb.general.thickness

        def _path(obj):
            return Part.makeCompound(_face(obj).Wires)

        def _face(obj):
            return makeFace(obj,fuse)

        def _solid(obj):
            return _face(obj).extrude(Vector(0,0,thickness))

        try:
            func = locals()['_{}'.format(shape_type)]
        except KeyError:
            raise ValueError('invalid shape type: {}'.format(shape_type))

        oval_count = 0
        count = 0
        skip_count = 0
        doc = getActiveDoc()
        for m in self.pcb.module:
            m_at,m_angle = getAt(m.at)
            for p in m.pad:
                if 'drill' not in p:
                    continue
                if p.drill.oval:
                    if not oval:
                        continue
                    size = Vector(p.drill[0],p.drill[1])
                    w = make_oval(size+Vector(delta,delta))
                    ovals[min(size.x,size.y)].append(w)
                    oval_count += 1
                elif p.drill[0]>=minSize and \
                        (not maxSize or p.drill[0]<=maxSize):
                    w = make_circle(Vector(p.drill[0]+delta))
                    holes[p.drill[0]].append(w)
                    count += 1
                else:
                    skip_count += 1
                    continue
                at,angle = getAt(p.at)
                if not m_angle and angle:
                    w.rotate(Vector(),Vector(0,0,1),angle)
                w.translate(at)
                if m_angle:
                    w.rotate(Vector(),Vector(0,0,1),m_angle)
                w.translate(m_at)
        self._log('pad holes: {}, skipped: {}',count+skip_count,skip_count)
        if oval:
            self._log('oval holes: {}',oval_count)

        skip_count = 0
        for v in self.pcb.via:
            if v.drill>=minSize and (not maxSize or v.drill<=maxSize):
                w = make_circle(Vector(v.drill))
                holes[v.drill].append(w)
                w.translate(makeVect(v.at))
            else:
                skip_count += 1
        self._log('via holes: {}, skipped: {}',len(self.pcb.via),skip_count)
        self._log('total holes added: {}',
                count+oval_count+len(self.pcb.via)-skip_count)

        ret = []
        for r in ((ovals,'oval'),(holes,'hole')):
            if not r[0]:
                continue
            objs = []
            for (width,rs) in iteritems(r[0]):
                shape = func(rs)
                if not add_feature:
                    objs.append(shape)
                else:
                    obj = doc.addObject('Part::Feature',
                                        '{}_{}'.format(r[1],shape_type))
                    obj.Label = '{}_{:.3f}'.format(obj.Name,width)
                    obj.Shape = shape
                    objs.append(obj)
            ret.append(self._makeCompound(
                objs,r[1],shape_type,fuse,add_feature))
        self._popLog('holes done')
        return ret

    def makePads(self, shape_type='face', thickness=0.05,
                    fuse=True,add_feature=True,prefix=''):

        self._pushLog('making pads...',prefix=prefix)

        def _path(objs):
            return Part.makeCompound(_face(objs).Wires)

        def _face(objs):
            return makeFace(objs,fuse)

        def _solid(objs):
            return _face(objs).extrude(Vector(0,0,thickness))

        try:
            func = locals()['_{}'.format(shape_type)]
        except KeyError:
            raise ValueError('invalid shape type: {}'.format(shape_type))

        layer_match = '*.{}'.format(self.layer.split('.')[-1])

        doc = getActiveDoc()
        objs = []

        count = 0
        skip_count = 0
        for m in self.pcb.module:
            ref = ''
            for t in m.fp_text:
                if t[0] == 'reference':
                    ref = t[1]
                    break;
            m_at,m_angle = getAt(m.at)
            pads = []
            count += len(m.pad)
            for p in m.pad:
                if self.layer not in p.layers \
                    and layer_match not in p.layers \
                    and '*' not in p.layers:
                    skip_count+=1
                    continue
                shape = p[2]

                try:
                    make_shape = globals()['make_{}'.format(shape)]
                except KeyError:
                    raise NotImplementedError(
                            'pad shape {} not implemented\n'.format(shape))

                w = make_shape(Vector(*p.size))
                at,angle = getAt(p.at)
                if not m_angle and angle:
                    w.rotate(Vector(),Vector(0,0,1),angle)
                w.translate(at)
                pads.append(w)

            if not pads:
                continue

            pads = func(pads)
            if m_angle:
                pads.rotate(Vector(),Vector(0,0,1),m_angle)
            pads.translate(m_at)

            if not add_feature:
                objs.append(pads)
            else:
                obj = doc.addObject('Part::Feature','pad_{}'.format(shape_type))
                label = '{}_{}_{}'.format(obj.Name,self.layer,ref)
                obj.Label = label
                obj.Shape = pads
                objs.append(obj)

        via_skip = 0
        vias = []
        for v in self.pcb.via:
            if self.layer not in v.layers:
                via_skip += 1
                continue
            w = make_circle(Vector(v.size))
            w.translate(makeVect(v.at))
            vias.append(w)
        if vias:
            shape = func(vias)
            if not add_feature:
                objs.append(shape)
            else:
                obj = doc.addObject('Part::Feature',
                        'vias_{}'.format(shape_type))
                obj.Shape = shape
                objs.append(obj)

        self._log('module: {}',len(self.pcb.module))
        self._log('pad: {}, skipped: {}',count,skip_count)
        self._log('via: {}, skipped: {}',len(self.pcb.via),via_skip)
        self._log('total pad added: {}',
                count-skip_count+len(self.pcb.via)-via_skip)

        objs = self._makeCompound(objs,'pads',shape_type,fuse,add_feature)
        self._popLog('pads done')
        return objs

    def makeTracks(self,shape_type='face',thickness=0.05,fuse=True,
                    connect=True,add_feature=True,prefix=''):
        self._pushLog('making tracks...',prefix=prefix)

        width = 0

        def _wire(obj):
            return obj

        def _path(obj):
            return Part.makeCompound(_face(obj).Wires)

        # Tracks are different from others in that the primary shape is open
        # wires.  _path here is used to converted the wire to thick lines. It
        # will offset the wire by track width 
        def _face(obj):
            ret = []
            for o in (obj if isinstance(obj,(list,tuple)) else (obj,)):
                if len(o.Edges)>1:
                    try:
                        # Part.makeOffset2D() requires that the input wires can
                        # determine a plane. So we have to manually handle a
                        # single edge or colinear edges.  Hece, the exception
                        # handling here.
                        ret.append(o.makeOffset2D(
                            width*0.5,algo=3,openResult=True))
                        continue
                    except Exception as e:
                        self._log('Track offset failed. Use fallback',
                                    level='warning')
                for e in o.Edges:
                    ret.append(makeThickLine(e.Vertexes[0].Point,
                                e.Vertexes[1].Point,width*0.5))

            # Now that all wires are thickened and closed, we can use makeFace
            # to handle the rest
            return makeFace(ret,fuse)

        def _solid(obj):
            return _face(obj).extrude(Vector(0,0,thickness))

        try:
            func = locals()['_{}'.format(shape_type)]
        except KeyError:
            raise ValueError('invalid shape type: {}'.format(shape_type))

        doc = getActiveDoc()
        tracks = defaultdict(list)
        count = 0
        for s in self.pcb.segment:
            if s.layer == self.layer:
                tracks[s.width].append(s)
                count += 1

        objs = []
        i = 0
        for (width,ss) in iteritems(tracks):
            self._log('making {} tracks of width {:.2f}, ({}/{})',
                    len(ss),width,i,count)
            i+=len(ss)
            wires = []
            for s in ss:
                wires.append(Part.makeLine(makeVect(s.start),makeVect(s.end)))
            wires = findWires(wires)
            for w in wires:
                shape = func(w) if connect else func(w.Edges)
                if not add_feature:
                    objs.append(shape)
                else:
                    obj = doc.addObject('Part::Feature',
                            'track_{}'.format(shape_type))
                    obj.Label = '{}_{}_{:.2f}'.format(obj.Name,self.layer,width)
                    obj.Shape = shape
                    objs.append(obj)

        objs = self._makeCompound(objs,'tracks',shape_type,fuse,add_feature)
        self._popLog('tracks done')
        return objs


    def makeZones(self,shape_type='face',thickness=0.05,fuse=True,
            add_feature=True, prefix=''):

        self._pushLog('making zones...',prefix=prefix)
        z = None

        def _path(obj):
            return Part.makeCompound(_face(obj).Wires)

        def _face(obj):
            # NOTE: It is weird that kicad_pcb's zone fillpolygon is 0.127mm
            # thinner than the actual copper region shown in pcbnew or the
            # generated gerber. Why is this so? Is this 0.127 hardcoded or
            # related to some setup parameter? I am guessing this is half the
            # zone.min_thickness setting here.
            return makeFace(obj,False).makeOffset2D(
                    z.min_thickness*0.5,algo=3,intersection=True)

        def _solid(obj):
            _face(obj).extrude(thickness)

        try:
            func = locals()['_{}'.format(shape_type)]
        except KeyError:
            raise ValueError('invalid shape type: {}'.format(shape_type))

        doc = getActiveDoc()
        objs = []
        for z in self.pcb.zone:
            if z.layer != self.layer:
                continue
            count = len(z.filled_polygon)
            self._pushLog('making zone {}...', z.net_name)
            for idx,p in enumerate(z.filled_polygon):
                holes = []
                table = {}
                pts = SexpList(p.pts.xy)

                # close the polygon
                pts._append(p.pts.xy._get(0))

                # `table` uses a pair of vertex as the key to store the index of
                # an edge.
                for i in xrange(len(pts)-1):
                    table[str((pts[i],pts[i+1]))] = i

                # This is how kicad represents holes in zone polygon
                #  ---------------------------
                #  |    -----      ----      |
                #  |    |   |======|  |      |
                #  |====|   |      |  |      |
                #  |    -----      ----      |
                #  |                         |
                #  ---------------------------
                # It uses a single polygon with coincide edges of oppsite
                # direction (shown with '=' above) to dig a hole. And one hole
                # can lead to another, and so forth. The following `build()`
                # function is used to recursively discover those holes, and
                # cancel out those '=' double edges, which will surely cause
                # problem if left alone. The algorithm assumes we start with a
                # point of the outer polygon. 
                def build(start,end):
                    results = []
                    while start<end:
                        # We used the reverse edge as key to search for an
                        # identical edge of oppsite direction. NOTE: the
                        # algorithm fails if this assumption is not true, i.e.
                        # those hole digging double edges are in pair without
                        # branch in the middle
                        key = str((pts[start+1],pts[start]))
                        try:
                            i = table[key]
                            del table[key]
                        except KeyError:
                            # `KeyError` means its a normal edge, add the line.
                            results.append(Part.makeLine(
                                makeVect(pts[start]),makeVect(pts[start+1])))
                            start += 1
                            continue

                        # We found the start of a double edge, treat all edges
                        # in between as holes and recurse. Both of the double
                        # edges are skipped.
                        h = build(start+1,i)
                        if h:
                            holes.append(Part.Wire(h))
                        start = i+1
                    return results

                edges = build(0,len(pts)-1)

                self._log('region {}/{}, holes: {}',idx+1,count,len(holes))

                shape = func([Part.Wire(edges)]+holes,True)
                if not add_feature:
                    obj = shape
                else:
                    obj = doc.addObject(
                                'Part::Feature','zone_{}'.format(shape_type))
                    obj.Label = '{}_{}_{}'.format(
                                    obj.Name,self.layer,z.net_name)
                    obj.Shape = shape
                objs.append(obj)
            self._popLog()

        objs = self._makeCompound(objs,'zones',shape_type,fuse,add_feature)
        self._popLog('zones done')
        return objs

    def makeCopper(self,shape_type='face',thickness=0.05,fuse=True,
                    z=0, add_feature=True,add_sub_feature=False,prefix=''):

        self._pushLog('making copper layer {}...',self.layer,prefix=prefix)

        def getObj(obj):
            if isinstance(obj,(list,tuple)):
                return getObj(obj[0])
            return obj

        objs = []
        for name in ('Pads','Zones','Tracks'):
            subobjs = getattr(self,'make{}'.format(name))(
                        shape_type=shape_type,thickness=thickness,
                        fuse=False,add_feature=add_sub_feature,prefix=None)
            objs.append(getObj(subobjs))

        objs = self._makeCompound(objs,'copper',shape_type,fuse,add_feature)
        obj = getObj(objs)
        if isinstance(obj,Part.Shape):
            obj.translate(Vector(0,0,z))
        else:
            obj.Placement = Placement(Vector(0,0,z),Rotation())

        self._popLog('done copper layer {}',self.layer)
        return objs

