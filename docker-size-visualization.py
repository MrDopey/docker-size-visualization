from typing import List, Dict

import docker
import click
import graphviz
import hashlib

client = docker.from_env()

class LayerImage:
    def __init__(self, id: str, size: int, comment: str, created: int, created_by: str, tags: List[str]):
        self.id = id
        self.size = size
        self.comment = comment
        self.created = created
        self.created_by = created_by
        self.tags = tags
        self.subtotal = 0
        self.running_total = 0
        self.children: List[LayerImage] = []

    def name(self) -> str:
        # if self.id != '<missing>':
        #     return self.id # TODO: self.id has sha256:123 prefix which messes with the graph
        # else:
            id_key = self.created_by + str(self.size) + str(self.created)
            return 'cust-' + hashlib.md5(id_key.encode('utf-8')).hexdigest()
    
    def graph_label(self) -> str:
         # Use docker tag if it exists otherwise the command that was used to create the layer
        if self.tags is not None and len(self.tags) > 0:
            res = ''

            tags = set([ t.split(':')[1] for t in self.tags ])
            return ','.join(tags)
        else:
            return self.created_by[0:40]

    def set_subtotal(self, size: int):
        self.subtotal = size
    
    def set_running_total(self, size: int):
        self.running_total = size

    def tooltip(self) -> str:
        things_to_include=[
            f'subtotal ratio: {self.sub_total_ratio()}',
            f'layer size: {format_number(self.size)}',
            f'subtotal size: {format_number(self.subtotal)}',
            f'running total: {format_number(self.running_total)}',
            self.created_by
        ]

        return '\n'.join(things_to_include)

    def sub_total_ratio(self) -> str:
        if self.subtotal == 0:
            return 0
        else:
            return self.size / self.subtotal

    def sub_total_ratio_display(self) -> str:
        if self.subtotal == 0:
            return '0.00001'
        else:
            res = self.size / self.subtotal
            # extremes of 1 or 0 gives a weird gradient in graphviz
            # so just give it a number close enough to either end
            if self.size == 0:
                return '0.0001'
            if res == 1:
                return '0.9999'
            else:
                return str(res)


    def __repr__(self):
       return self.pretty_print(0, 0, 0, '')

    def pretty_print(self, depth: int, total: int, subTotal: int, indent: str = '') -> str:
        description = self.created_by[0:]

        tags = ', Tags: ' + ','.join(self.tags) if self.tags else ''
        newTotal = total + self.size
        newSubtotal = subTotal + self.size

        result = f"{indent}Running total: {format_number(newTotal)}, Subtotal: {format_number(newSubtotal)}, Layer size: {format_number(self.size)} Desc: {description} {tags}\n"
        many_children = len(self.children) > 1
        for i, child in enumerate(self.children):
            childDepth = depth + 1 if many_children else depth
            childSubtotal = 0 if many_children else newSubtotal
            child_indent = indent + ('   ' + str(childDepth) + '.' + str(i) +'   ' if many_children else '')
            # child_indent = indent + '  │  ' * (len(self.children) - 1)
            result += child.pretty_print(childDepth, newTotal, childSubtotal, child_indent)


        # result += "\n"
        return result

    def add_next_layer(self, next_layer: 'LayerImage'):
        self.children.append(next_layer)

    def isSameLayer(self, other: 'LayerImage') -> bool:
        return (self.id != '<missing>' and self.id == other.id) or (
         self.created_by == other.created_by 
          and  self.size == other.size
          and self.created == other.created)

def format_number(size: int) -> str: 
    kbNum = size / 1024
    mbNum = kbNum / 1024

    res = 0
    suffix = "zzz"
    if size < 10:
        res = size
        suffix = "b"
    elif kbNum < 10:
       res = kbNum
       suffix = "kb"
    else:
        res = mbNum
        suffix = "mb"

    formatNum = round(res, 2)
    return f"{formatNum}{suffix}"

def get_layer_tree(imageName: str) -> ( str, LayerImage):
    """
    Get a tree structure of the layers in an image.

    Args:
        image: The image to get the layer tree for.

    Returns:
        A dictionary representing the layer tree.
    """
    image: docker.models.images.Image = client.images.get(imageName)

    history: List[LayerImage] = []
    previous_layer = None
    top_layer = None

    hist = image.history()
    hist.reverse() # Oldest first
    
    for layer_data in hist:
        layer = LayerImage(
            layer_data['Id'], 
            layer_data['Size'], 
            layer_data['Comment'],
            layer_data['Created'],
            layer_data['CreatedBy'],
            layer_data['Tags']
        )
        history.append(layer)

        if previous_layer:
            previous_layer.add_next_layer(layer)
        else:
            top_layer = layer
        
        previous_layer = layer

    return ( imageName, top_layer)

def compare(dict: dict[str, LayerImage]):
    first, *rest = dict

    root = [ dict[first] ]

    for v in rest:
      isMatched = False
      for r in root:
         if crawl(r, dict[v]):
            isMatched = True
      if not isMatched:
         root.append(dict[v])

    return root


# Returns if there are matches
def crawl(image1: LayerImage, image2: LayerImage) -> bool:
  if (image1.isSameLayer(image2)):
    childrenMatch = doChildMatch(image1, image2)
    if not childrenMatch:
      for a in image2.children:
        image1.add_next_layer(a)

    return True 
  else: # These are not the same layer, so they diverge
    return False

def doChildMatch(image1: LayerImage, image2: LayerImage) -> bool:
    for child1 in image1.children:
      for child2 in image2.children:
        if crawl(child1, child2):
           return True
    return False

def populate_subtotal(layer: LayerImage, sub_total_parents: List[LayerImage], subtotal: int, running_total: int):
    sub_total_parents.append(layer)

    subtotal += layer.size
    running_total += layer.size

    layer.set_running_total(running_total)
    not_single_child = len(layer.children) != 1

    if not_single_child:
        for sub_parent in sub_total_parents:
            sub_parent.set_subtotal(subtotal) 

    for child in layer.children:
        next_subtotal_parents = [] if not_single_child else sub_total_parents
        next_subtotal = 0 if not_single_child else subtotal
        populate_subtotal(child, next_subtotal_parents, subtotal=next_subtotal, running_total=running_total)


def populate_graph(dot: graphviz.Digraph, layer_tree: List[LayerImage]):
    for layer in layer_tree:
        shape = 'doublecircle' if layer.tags is not None and len(layer.tags) > 0 else 'oval'
        dot.node(
            layer.name(),
            label=layer.graph_label(),
            tooltip=layer.tooltip(),
            shape=shape,
            style='filled',
            fillcolor=f'yellow;{layer.sub_total_ratio_display()}:transparent'
        )

        populate_graph(dot, layer.children)
       
        for child in layer.children:
            dot.edge(layer.name(), child.name())

@click.command()
@click.option("-rn", "--repository-name", required=True, type=str)
@click.option("-v", "--versions", required=True, multiple=True)
def do_thing(repository_name, versions):
    image_tags=list(map(lambda x : repository_name + ':' + x, versions))
    print(image_tags)

    list_of_layers = dict(get_layer_tree(i) for i in image_tags)
    layer_tree = compare(list_of_layers)
    
    for l in layer_tree:
        populate_subtotal(l, [], 0, 0)


    dot = graphviz.Digraph(comment="bobs", format="svg")
    populate_graph(dot, layer_tree)

    dot.render(directory='output/' + repository_name).replace('\\', '/')

if __name__ == '__main__':
    do_thing()