from __future__ import (absolute_import, division,
        print_function, unicode_literals)
from builtins import *
from future.utils import iteritems

from collections import defaultdict
from math import sqrt, atan2, degrees, sin, cos, radians, pi, hypot
import traceback
import FreeCAD
import FreeCADGui
import Part
from FreeCAD import Console,Vector,Placement,Rotation
import DraftGeomUtils
import DraftVecUtils

import sys, os
sys.path.append(os.path.dirname(os.path.realpath(__file__)))
from kicad_parser import KicadPCB,SexpList

import logging

class FCADLogger:
    def isEnabledFor(self,__):
        return True

    def debug(self,msg):
        Console.PrintLog(msg+'\n')
        FreeCADGui.updateGui()

    def info(self,msg):
        Console.PrintMessage(msg+'\n')
        FreeCADGui.updateGui()

    def error(self,msg):
        Console.PrintError(msg+'\n')
        FreeCADGui.updateGui()

    def warning(self,msg):
        Console.PrintWarning(msg+'\n')
        FreeCADGui.updateGui()

logger = FCADLogger()
#  logger = logging.getLogger(__name__)

def getActiveDoc():
    if FreeCAD.ActiveDocument is None:
        return FreeCAD.newDocument('kicad_fcad')
    return FreeCAD.ActiveDocument

def isZero(f):
    return round(f,DraftGeomUtils.precision())==0

def makeColor(*color):
    if len(color)==1:
        color = color[0]
        r = float((color>>24)&0xFF)
        g = float((color>>16)&0xFF)
        b = float((color>>8)&0xFF)
    else:
        r,g,b = color
    return (r/255.0,g/255.0,b/255.0)

def makeVect(l):
    return Vector(l[0],-l[1],0)

def getAt(at):
    v = makeVect(at)
    return (v,0) if len(at)==2 else (v,at[2])

def product(v1,v2):
    return Vector(v1.x*v2.x,v1.y*v2.y,v1.z*v2.z)

def make_rect(size):
    return Part.makePolygon([product(size,Vector(*v))
        for v in ((-0.5,-0.5),(0.5,-0.5),(0.5,0.5),(-0.5,0.5),(-0.5,-0.5))])

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

def findWires(edges):
    try:
        return [Part.Wire(e) for e in Part.sortEdges(edges)]
    except AttributeError:
        msg = 'Missing Part.sortEdges.'\
            'You need newer FreeCAD (0.17 git 799c43d2)'
        logger.error(msg)
        raise AttributeError(msg)

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


def unpack(obj):
    if not obj:
        raise ValueError('null shape')

    if isinstance(obj,(list,tuple)) and len(obj)==1:
        return obj[0]
    return obj


def getKicadPath():
    if sys.platform != 'win32':
        path='/usr/share/kicad/modules/packages3d'
        if not os.path.isdir(path):
            path = '/usr/local/share/kicad/modules/packages3d'
        return path

    import re
    confpath = os.path.join(os.path.abspath(os.environ['APPDATA']),'kicad')
    kicad_common = os.path.join(confpath,'kicad_common')
    if not os.path.isfile(kicad_common):
        logger.warning('cannot find kicad_common')
        return None
    with open(kicad_common,'rb') as f:
        content = f.read()
    match = re.search(r'^\s*KISYS3DMOD\s*=\s*([^\r\n]+)',content,re.MULTILINE)
    if not match:
        logger.warning('no KISYS3DMOD found')
        return None

    return match.group(1).rstrip(' ')

_model_cache = {}

def clearModelCache():
    _model_cache = {}

def loadModel(filename):
    try:
        obj = _model_cache[filename]
        return obj
    except KeyError:
        pass

    import ImportGui
    doc = getActiveDoc()
    if not os.path.isfile(filename):
        return
    count = len(doc.Objects)
    dobjs = []
    try:
        ImportGui.insert(filename,doc.Name)
        dobjs = doc.Objects[count:]
        obj = doc.addObject('Part::Compound','tmp')
        obj.Links = dobjs
        obj.recompute()
        dobjs = [obj]+dobjs
        obj = (obj.Shape.copy(),obj.ViewObject.DiffuseColor)
        _model_cache[filename] = obj
        return obj
    except Exception as ex:
        logger.error('failed to load model: {}'.format(ex))
    finally:
        for o in dobjs:
            doc.removeObject(o.Name)

class KicadFcad:
    AlgoName = {0:'OCC',
                1:'libarea',
                2:'libareaNoArcFit',
                3:'ClipperOffset',
                4:'ClipperNoArcFit'}

    def __init__(self,filename=None,add_feature=True,algo='ClipperOffset',
            part_path = None):

        if not filename:
            filename = '/home/thunder/pwr.kicad_pcb'
        self.pcb = KicadPCB.load(filename)
        self.add_feature = add_feature
        self.prefix = ''
        self.indent = '  '
        self.algo = None
        for (i,name) in iteritems(self.AlgoName):
            if algo==name:
                self.algo = i
                break
        if self.algo is None:
            raise ValueError('Unknown offset algo: {}'.format(algo))
        self.colors = {
                'board':makeColor(0,150,0),
                'pad':{0:makeColor(204,204,204)},
                'zone':{0:makeColor(0,100,0)},
                'track':{0:makeColor(0,120,0)},
        }
        if part_path is not None:
            self.path = part_path
        else:
            self.path = getKicadPath()
        self.layer = 'F.Cu'
        self.layer_type = 0
        self.setLayer(0)
            

    def setLayer(self,layer):
        try:
            layer = int(layer)
        except:
            for layer_type in self.pcb.layers:
                if self.pcb.layers[layer_type][0] == layer:
                    self.layer = layer
                    self.layer_type = int(layer_type)
                    return
            raise KeyError('layer {} not found'.format(layer))
        else:
            if str(layer) not in self.pcb.layers:
                raise KeyError('layer {} not found'.format(layer))
            self.layer_type = layer
            self.layer = self.pcb.layers[str(layer)][0]


    def _log(self,msg,*arg,**kargs):
        level = 'info'
        if kargs:
            if 'level' in kargs:
                level = kargs['level']
        if not logger.isEnabledFor(getattr(logging,level.upper())):
            return
        getattr(logger,level)('{}{}'.format(self.prefix,msg.format(*arg)))


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


    def _makeObject(self,otype,name,
            label=None,links=None,shape=None):
        doc = getActiveDoc()
        obj = doc.addObject(otype,name)
        if self.layer:
            obj.Label = '{}#{}'.format(obj.Name,self.layer)
        if label is not None:
            obj.Label += '#{}'.format(label)
        if links is not None:
            setattr(obj,links,shape)
            for s in shape if isinstance(shape,(list,tuple)) else (shape,):
                if hasattr(s,'ViewObject'):
                    s.ViewObject.Visibility = False
            obj.recompute()
        return obj

    def _makeCompound(self,obj,name,label=None,
            fuse=False,fit_arc=False,add_feature=False,force=False):

        if fuse:
            return self._makeOffset(obj,name,0,fuse=True,
                    fit_arc=fit_arc,label=label)

        obj = unpack(obj)
        if not isinstance(obj,(list,tuple)):
            if not force:
                return obj
            obj = [obj]
        if add_feature or self.add_feature:
            return self._makeObject('Part::Compound',
                    '{}_combo'.format(name),label,'Links',obj)
        return Part.makeCompound(obj)


    def _makeOffset(self,obj,name,offset,fill=None, fuse=False,
            fit_arc=False,label=None,deleteOnFailure=False):

        obj = self._makeCompound(obj,name,label)

        if not self.add_feature:
            if fill is None:
                fill = True if obj.Faces else False
            return obj.makeOffset2D(offset,algo=self._algo(fit_arc),
                fill=fill, openResult=not len(obj.Faces), intersection=fuse)

        if offset==0:
            name = '{}_fuse'.format(name)
        nobj = self._makeObject('Part::Offset2D',name,label)
        nobj.Source = obj
        nobj.Value = offset
        nobj.Algo = self.AlgoName[self._algo(fit_arc)]
        nobj.Intersection = fuse
        if fill is None:
            fill = True if obj.Shape.Faces else False
        nobj.Fill = fill
        nobj.Mode = 'Pipe' if len(obj.Shape.Faces) else 'Skin'
        if not nobj.recompute():
            if deleteOnFailure:
                doc = nobj.Document
                doc.removeObject(nobj.Name)
                objs = obj.Links
                doc.removeObject(obj.Name)
                for o in objs:
                    doc.removeObject(o.Name)
            raise FreeCAD.Base.FreeCADError('offset2d failed')
        obj.ViewObject.Visibility = False
        return nobj


    def _makePath(self,obj,name,fill=False, fuse=False,
            fit_arc=False, label=None):

        if isinstance(obj,(list,tuple)):
            if self.add_feature:
                objs = []
                for o in obj:
                    if isinstance(o,Part.Shape):
                        objs.append(self._makeObject('Part::Feature',
                                '{}_wire'.format(name),label,'Shape',o))
                    else:
                        objs.append(o)
                obj = objs

            obj = self._makeCompound(obj,name,label,fuse,fit_arc)

        elif self.add_feature and isinstance(obj,Part.Shape):
            obj = self._makeObject('Part::Feature', '{}_wire'.format(name),
                    label,'Shape',obj)

        if not fill:
            return obj

        if self.add_feature:
            return self._makeObject('Part::Face',
                    '{}_face'.format(name),label,'Sources',obj)
        else:
            return Part.makeFace(obj.Wires,'Part::FaceMakerBullseye')


    def _makeSolid(self,obj,name,height,label=None, fuse=False,fit_arc=False):

        obj = self._makeCompound(obj,name,label,fuse,fit_arc)

        if not self.add_feature:
            return obj.extrude(Vector(0,0,height))

        nobj = self._makeObject(
                'Part::Extrusion','{}_solid'.format(name),label)
        nobj.Base = obj
        nobj.Dir = Vector(0,0,height)
        obj.ViewObject.Visibility = False
        nobj.recompute()
        return nobj

    def _place(self,obj,pos,angle=None):
        if not self.add_feature:
            if angle:
                obj.rotate(Vector(),Vector(0,0,1),angle)
            obj.translate(pos)
        else:
            r = Rotation(Vector(0,0,1),angle) if angle else Rotation()
            obj.Placement = Placement(pos,r)


    def makeBoard(self,shape_type='solid',thickness=None,fuse=True,fit_arc=True,
            minHoleSize=0,ovalHole=True,prefix=''):

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

        holes = None
        if ovalHole or minHoleSize is not None:
            self._pushLog()
            holes = self.makeHoles(shape_type='path', fuse=False,
                    minSize=minHoleSize, oval=True, prefix=None)
            self._popLog()

        if not thickness:
            thickness = self.pcb.general.thickness

        def _path(fill=False):
            if not holes:
                return self._makePath(wires,'board',fill=fill,
                        fuse=fuse,fit_arc=fit_arc)

            obj = self._makePath(wires,'outline',fill=False)
            return self._makePath((obj,holes),'board',fill=fill,
                    fuse=fuse,fit_arc=fit_arc)

        def _face():
            return _path(True)

        def _solid():
            return self._makeSolid(_face(),'board',thickness)

        try:
            func = locals()['_{}'.format(shape_type)]
        except KeyError:
            raise ValueError('invalid shape type: {}'.format(shape_type))

        obj = func()
        if self.add_feature:
            obj.ViewObject.ShapeColor = self.colors['board']

        self._popLog('board done')
        return obj


    def makeHoles(self,shape_type='face',fuse=False, fit_arc=True,
            thickness=None,minSize=0,maxSize=0,oval=False,prefix=''):

        self._pushLog('making holes...',prefix=prefix)

        holes = defaultdict(list)
        ovals = defaultdict(list)

        width=0
        def _path(obj,name,fill=False):
            return self._makePath(obj,name,fill=fill,fuse=False, label=width)

        def _face(obj,name):
            return _path(obj,name,True)

        _solid = _face

        try:
            func = locals()['_{}'.format(shape_type)]
        except KeyError:
            raise ValueError('invalid shape type: {}'.format(shape_type))

        oval_count = 0
        count = 0
        skip_count = 0
        for m in self.pcb.module:
            m_at,m_angle = getAt(m.at)
            for p in m.pad:
                if 'drill' not in p:
                    continue
                if p.drill.oval:
                    if not oval:
                        continue
                    size = Vector(p.drill[0],p.drill[1])
                    w = make_oval(size)
                    ovals[min(size.x,size.y)].append(w)
                    oval_count += 1
                elif p.drill[0]>=minSize and \
                        (not maxSize or p.drill[0]<=maxSize):
                    w = make_circle(Vector(p.drill[0]))
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

        objs = []
        for r in ((ovals,'oval'),(holes,'hole')):
            if not r[0]:
                continue
            for (width,rs) in iteritems(r[0]):
                objs.append(func(rs,r[1]))

        if objs:
            if shape_type=='solid':
                z = 0
                if not thickness:
                    thickness = 2*self.pcb.general.thickness
                    z = -thickness*0.25
                objs = self._makeSolid(objs,'holes',
                        thickness,fuse=fuse,fit_arc=fit_arc)
                self._place(objs,Vector(0,0,z))
            else:
                objs = self._makeCompound(objs,'holes',fuse=fuse,fit_arc=fit_arc)

        self._popLog('holes done')
        return objs

    def makePads(self, shape_type='face', thickness=0.05,
                fuse=True,fit_arc=True,prefix=''):

        self._pushLog('making pads...',prefix=prefix)

        def _path(obj,name,label=None,fill=False):
            return self._makePath(obj,name,fill=fill,label=label)

        def _face(obj,name,label=None):
            return _path(obj,name,label,True)

        _solid = _face

        try:
            func = locals()['_{}'.format(shape_type)]
        except KeyError:
            raise ValueError('invalid shape type: {}'.format(shape_type))

        layer_match = '*.{}'.format(self.layer.split('.')[-1])

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
                pads.append(func(w,'pad','{}#{}'.format(p[0],ref)))

            if not pads:
                continue

            obj = self._makeCompound(pads,'pads',ref)
            self._place(obj,m_at,m_angle)
            objs.append(obj)

        via_skip = 0
        vias = []
        for v in self.pcb.via:
            if self.layer not in v.layers:
                via_skip += 1
                continue
            w = make_circle(Vector(v.size))
            w.translate(makeVect(v.at))
            vias.append(func(w,'via'))

        if vias:
            objs.append(self._makeCompound(vias,'vias'))

        self._log('modules: {}',len(self.pcb.module))
        self._log('pads: {}, skipped: {}',count,skip_count)
        self._log('vias: {}, skipped: {}',len(self.pcb.via),via_skip)
        self._log('total pads added: {}',
                count-skip_count+len(self.pcb.via)-via_skip)

        if objs:
            if shape_type=='solid':
                objs = self._makeSolid(objs,'pads',
                        thickness,fuse=fuse,fit_arc=fit_arc)
            else:
                objs = self._makeCompound(objs,'pads',fuse=fuse,fit_arc=fit_arc)
            self.setColor(objs,'pad')

        self._popLog('pads done')
        return objs


    def setColor(self,obj,otype):
        if not self.add_feature:
            return
        try:
            color = self.colors[otype][self.layer_type]
        except KeyError:
            color = self.colors[otype][0]
        obj.ViewObject.ShapeColor = color


    def makeTracks(self,shape_type='face',thickness=0.05,fuse=True,
                fit_arc=True,connect=True,prefix=''):

        self._pushLog('making tracks...',prefix=prefix)

        width = 0
        if shape_type=='wire':
            fuse = False

        def _wire(edges):
            if connect:
                wires = findWires(edges)
            else:
                wires = [Part.Wire(e) for e in edges]
            return self._makePath(wires,'track',label=width)

        def _path(edges,fill=False):
            obj = _wire(edges)
            if self.add_feature:
                wires = obj.Shape.Wires
            else:
                wires = obj
            if connect:
                try:
                    # Part.makeOffset2D() requires that the input wires can
                    # determine a plane. So we have to manually handle a
                    # single edge or colinear edges.  Hece, the exception
                    # handling here.
                    return self._makeOffset(obj,'track',width*0.5, fuse=True,
                            fill=fill, label=width, deleteOnFailure=False)
                except Exception as e:
                    self._log('Track offset failed: {}\n'
                              'Use fallback'.format(e), level='warning')
            objs = []
            for w in wires:
                tracks = []
                for e in w.Edges:
                    e = makeThickLine(e.Vertexes[0].Point,
                                e.Vertexes[1].Point,width*0.5)
                    tracks.append(self._makePath(
                        e,'track',fill=fill,label=width))
                objs.append(self._makeCompound(tracks,'tracks',width))

            return self._makeCompound(objs,'tracks',width)

        def _face(edges):
            return _path(edges,True)

        _solid = _face

        try:
            func = locals()['_{}'.format(shape_type)]
        except KeyError:
            raise ValueError('invalid shape type: {}'.format(shape_type))

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
            edges = []
            for s in ss:
                edges.append(Part.makeLine(makeVect(s.start),makeVect(s.end)))
            objs.append(func(edges))

        if shape_type == 'solid':
            objs = self._makeSolid(objs,'tracks',thickness,
                    fuse=fuse,fit_arc=fit_arc)
        else:
            objs = self._makeCompound(objs,'tracks',fuse=fuse,fit_arc=fit_arc)
        self.setColor(objs,'track')
        self._popLog('tracks done')
        return objs

    def _algo(self,fit_arc=False):
        if self.algo==0:
            return self.algo
        return self.algo if fit_arc else ((self.algo-1)|1)+1

    def makeZones(self,shape_type='face',thickness=0.05,fuse=True,
            fit_arc=True, prefix=''):

        self._pushLog('making zones...',prefix=prefix)

        z = None
        holes = None

        def _path(obj,fill=False):
            outline = self._makePath(obj,'zone_outline',label=z.net_name)
            if holes:
                outline = (outline,self._makePath(
                    holes,'zone_hole',label=z.net_name))

            # NOTE: It is weird that kicad_pcb's zone fillpolygon is 0.127mm
            # thinner than the actual copper region shown in pcbnew or the
            # generated gerber. Why is this so? Is this 0.127 hardcoded or
            # related to some setup parameter? I am guessing this is half the
            # zone.min_thickness setting here.
            return self._makeOffset(outline,'zone',z.min_thickness*0.5,
                    label=z.net_name, fill=fill, fuse=True)

        def _face(obj):
            return _path(obj,True)

        _solid = _face

        try:
            func = locals()['_{}'.format(shape_type)]
        except KeyError:
            raise ValueError('invalid shape type: {}'.format(shape_type))

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
                        # algorithm only works if the following assumption is
                        # true, that those hole digging double edges are of
                        # equal length without any branch in the middle
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

                objs.append(func(Part.Wire(edges)))

            self._popLog()

        if shape_type == 'solid':
            objs = self._makeSolid(objs,'zones',thickness,
                    fuse=fuse,fit_arc=fit_arc)
        else:
            objs = self._makeCompound(objs,'zones',fuse=fuse,fit_arc=fit_arc)
        self.setColor(objs,'zone')
        self._popLog('zones done')
        return objs

    def isBottomLayer(self):
        return self.layer_type == 31

    def makeCopper(self,shape_type='face',thickness=0.05,withHoles=False,
            fuse=True, fit_arc=True,z=0, prefix=''):

        self._pushLog('making copper layer {}...',self.layer,prefix=prefix)

        if shape_type == 'solid':
            sub_fuse = fuse
            fuse = False
        else:
            sub_fuse = False

        objs = []
        for name in ('Pads','Zones','Tracks'):
            objs.append(getattr(self,'make{}'.format(name))(
                        shape_type=shape_type,thickness=thickness,
                        fuse=sub_fuse,fit_arc=fit_arc,prefix=None))

        if shape_type=='solid':
            if self.isBottomLayer():
                offset = -thickness
            else:
                offset = thickness
            self._place(objs[0],Vector(0,0,offset))

        obj = self._makeCompound(objs,'copper',
                fuse=fuse,fit_arc=fit_arc)

        self._place(obj,Vector(0,0,z))

        self._popLog('done copper layer {}',self.layer)
        return obj

    def makeCoppers(self,shape_type='face',thickness=0.05,withHoles=False,
            fuse=True, fit_arc=True, prefix=''):

        self._pushLog('making all copper layers...',prefix=prefix)

        layer_save = self.layer
        objs = []
        layers = []
        for i in xrange(0,32):
            if str(i) in self.pcb.layers:
                layers.append(i)
        if not layers:
            raise ValueError('no copper layer found')

        z = self.pcb.general.thickness+thickness
        if len(layers) == 1:
            z_step = 0
        else:
            z_step = (z+thickness)/(len(layers)-1)

        try:
            for layer in layers:
                self.setLayer(layer)
                objs.append(self.makeCopper(shape_type,thickness,
                    withHoles,fuse,fit_arc,z,None))
                z -= z_step
        finally:
            self.setLayer(layer_save)

        self._popLog('done making all copper layers')
        return objs

    def loadParts(self,z=0,combo=False,prefix=''):

        if not os.path.isdir(self.path):
            raise Exception('cannot find kicad package3d directory')

        self._pushLog('loading parts on layer {}...',self.layer,prefix=prefix)
        self._log('Kicad package3d path: {}',self.path)

        at_bottom = self.isBottomLayer()
        if z == 0:
            if at_bottom:
                z = -0.1
            else:
                z = self.pcb.general.thickness + 0.1

        if self.add_feature:
            parts = []
        else:
            parts = {}

        for (module_idx,m) in enumerate(self.pcb.module):
            if m.layer != self.layer:
                continue
            ref = '?'
            value = '?'
            for t in m.fp_text:
                if t[0] == 'reference':
                    ref = t[1]
                if t[0] == 'value':
                    value = t[1]

            m_at,m_angle = getAt(m.at)
            m_at += Vector(0,0,z)
            objs = []
            for (model_idx,model) in enumerate(m.model):
                path = os.path.splitext(model[0])[0]
                self._log('loading model {}/{} {} {} {}...',
                        model_idx,len(m.model), ref,value,model[0])
                for e in ('.stp','.STP','.step','.STEP'):
                    filename = os.path.join(self.path,path+e)
                    mobj = loadModel(filename)
                    if not mobj:
                        continue
                    at = product(Vector(*model.at.xyz),Vector(25.4,25.4,25.4))
                    rot = [-float(v) for v in reversed(model.rotate.xyz)]
                    pln = Placement(at,Rotation(*rot))
                    if not self.add_feature:
                        objs.append({'pos':pln,
                            'shape':mobj[0].copy(),'color':mobj[1]})
                    else:
                        obj = self._makeObject('Part::Feature','model',
                            label='{}#{}#{}'.format(module_idx,model_idx,ref),
                            links='Shape',shape=mobj[0])
                        obj.ViewObject.DiffuseColor = mobj[1]
                        obj.Placement = pln
                        objs.append(obj)
                    self._log('loaded')
                    break

            if not objs:
                continue

            pln = Placement(m_at,Rotation(Vector(0,0,1),m_angle))
            if at_bottom:
                pln = pln.multiply(Placement(Vector(),
                                    Rotation(Vector(1,0,0),180)))

            if self.add_feature:
                label = '{}#{}'.format(module_idx,ref)
                obj = self._makeCompound(objs,'part',label,force=True)
                obj.Placement = pln
                parts.append(obj)
            else:
                parts[label] = {'pos':pln, 'models':objs}

        if parts:
            if self.add_feature:
                if combo:
                    parts = self._makeCompound(parts,'parts')
                else:
                    grp = self._makeObject('App::DocumentObjectGroup','parts')
                    for o in parts:
                        grp.addObject(o)
                    parts = grp

        self._popLog('done loading parts on layer {}',self.layer)
        return parts

    def loadAllParts(self,combo=False):
        layer = self.layer
        objs = []
        try:
            self.setLayer(0)
            objs.append(self.loadParts(combo=combo))
        except Exception as e:
            self._log('{}',e,level='error')
        try:
            self.setLayer(31)
            objs.append(self.loadParts(combo=combo))
        except Exception as e:
            self._log('{}',e,level='error')
        finally:
            self.setLayer(layer)
        return objs

    def _makeCut(self,base,tool,name=None,label=None):
        if not self.add_feature:
            return base.cut(tool)

        if not name:
            name = base.Name.split('_')[0]
        obj = self._makeObject('Part::Cut',name,label)
        obj.Base = base
        obj.Tool = tool
        obj.recompute()
        obj.ViewObject.ShapeColor = base.ViewObject.ShapeColor
        return obj


    def make(self,copper_thickness=0.05,
            board_thickness=0, combo=True):

        self._pushLog('making pcb...',prefix='')

        objs = []
        objs.append(self.makeBoard(prefix=None,thickness=board_thickness))

        objs += self.makeCoppers(shape_type='solid',withHoles=True,
                thickness=copper_thickness,prefix=None)

        objs += self.loadAllParts(combo=True)

        if combo:
            objs = self._makeCompound(objs,'pcb')

        self._popLog('done')
        return objs



