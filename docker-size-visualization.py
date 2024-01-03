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
            for tag in tags:
                res += (tag + ',')

            return res[:-1] # remove the last comma
        else:
            return self.created_by[0:40]

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


def populate_graph(dot: graphviz.Digraph, layer_tree: List[LayerImage]):
    for layer in layer_tree:
        dot.node(layer.name(), layer.graph_label())

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

    # print(layer_tree)

    dot = graphviz.Digraph(comment="bobs", format="svg")
    populate_graph(dot, layer_tree)

    dot.render(directory='output/' + repository_name).replace('\\', '/')

if __name__ == '__main__':
    do_thing()