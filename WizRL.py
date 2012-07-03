#!/usr/bin/python
#
#

import libtcodpy as libtcod
import math
import textwrap
import shelve

#actual size of the window
SCREEN_WIDTH = 50
SCREEN_HEIGHT = 35

#size of map
MAP_WIDTH = 25
MAP_HEIGHT = 25

#sizes and coordinates relevant for the GUI
BAR_WIDTH = 20
PANEL_HEIGHT = 10
PANEL_Y = SCREEN_HEIGHT - PANEL_HEIGHT
MSG_X = 0
MSG_WIDTH = SCREEN_WIDTH - MAP_WIDTH
MSG_HEIGHT = SCREEN_HEIGHT - 1
INVENTORY_WIDTH = 40
CHARACTER_SCREEN_WIDTH = 30
LEVEL_SCREEN_WIDTH = 40

#parameters for dungeon generator
ROOM_MAX_SIZE = 10
ROOM_MIN_SIZE = 6
MAX_ROOMS = 30

#spell values
HEAL_AMOUNT = 40
LIGHTNING_DAMAGE = 40
LIGHTNING_RANGE = 10
CONFUSE_RANGE = 8
CONFUSE_NUM_TURNS = 10
FIREBALL_RADIUS = 3
FIREBALL_DAMAGE = 25

MAX_SPELLS = 5

#experience and level-ups
LEVEL_UP_BASE = 200
LEVEL_UP_FACTOR = 150


FOV_ALGO = 0  # default FOV algorithm
FOV_LIGHT_WALLS = True  # light walls or not
TORCH_RADIUS = 100

LIMIT_FPS = 5  # 20 frames-per-second maximum


color_dark_wall = libtcod.Color(80, 80, 100)
color_light_wall = libtcod.Color(130, 130, 130)
color_dark_ground = libtcod.Color(140, 140, 150)
color_light_ground = libtcod.Color(180, 180, 180)

# initialize globals
objects = []
inventory = []
player = ''
fov_map = ''
level_map = ''
stairs = ''
game_msgs = []
game_state = ''
dungeon_level = ''
fov_recompute = ''


class Tile:

    #a tile of the map and its properties
    def __init__(self, blocked, block_sight=None):
        self.blocked = blocked

        #all tiles start unexplored
        self.explored = False

        # tiles start with 0 mana
        self.mana = 0

        #by default, if a tile is blocked, it also blocks sight
        if block_sight is None:
            block_sight = blocked
        self.block_sight = block_sight


class Rect:

    #a rectangle on the map. used to characterize a room.
    def __init__(self, x, y, w, h):
        self.x1 = x
        self.y1 = y
        self.x2 = x + w
        self.y2 = y + h

    def center(self):
        center_x = (self.x1 + self.x2) / 2
        center_y = (self.y1 + self.y2) / 2
        return (center_x, center_y)

    def intersect(self, other):
        #returns true if this rectangle intersects with another one
        return (self.x1 <= other.x2 and self.x2 >= other.x1 and
                self.y1 <= other.y2 and self.y2 >= other.y1)


class Object:

    #this is a generic object: the player, a monster, an item, the stairs...
    #it's always represented by a character on screen.
    def __init__(self, x, y, char, name, color, blocks=False, always_visible=False, fighter=None, wizard=None, ai=None, item=None):
        self.x = x
        self.y = y
        self.char = char
        self.name = name
        self.color = color
        self.blocks = blocks
        self.always_visible = always_visible
        self.fighter = fighter
        if self.fighter:  # let the fighter component know who owns it
            self.fighter.owner = self
        self.wizard = wizard
        if self.wizard:
            self.wizard.owner = self
        self.ai = ai
        if self.ai:  # let the AI component know who owns it
            self.ai.owner = self

        self.item = item
        if self.item:  # let the Item component know who owns it
            self.item.owner = self

    def move(self, dx, dy):
        #move by the given amount, if the destination is not blocked
        if not is_blocked(self.x + dx, self.y + dy):
            self.x += dx
            self.y += dy

    def move_towards(self, target_x, target_y):
        #vector from this object to the target, and distance
        dx = target_x - self.x
        dy = target_y - self.y
        distance = math.sqrt(dx ** 2 + dy ** 2)

        #normalize it to length 1 (preserving direction), then round it and
        #convert to integer so the movement is restricted to the map grid
        dx = int(round(dx / distance))
        dy = int(round(dy / distance))
        self.move(dx, dy)

    def distance_to(self, other):
        #return the distance to another object
        dx = other.x - self.x
        dy = other.y - self.y
        return math.sqrt(dx ** 2 + dy ** 2)

    def distance(self, x, y):
        #return the distance to some coordinates
        return math.sqrt((x - self.x) ** 2 + (y - self.y) ** 2)

    def send_to_back(self):
        # make this object be drawn first, so all others appear above it if they're in the same tile.
        global objects
        objects.remove(self)
        objects.insert(0, self)

    def draw(self):
        #only show if it's visible to the player; or it's set to "always visible" and on an explored tile
        if (libtcod.map_is_in_fov(fov_map, self.x, self.y) or
            (self.always_visible and level_map[self.x][self.y].explored)):
            #set the color and then draw the character that represents this object at its position
            libtcod.console_set_default_foreground(con, self.color)
            libtcod.console_put_char(con, self.x, self.y, self.char, libtcod.BKGND_NONE)

    def clear(self):
        #erase the character that represents this object
        libtcod.console_put_char(con, self.x, self.y, ' ', libtcod.BKGND_NONE)


class Wizard:

    def __init__(self):
        self.mana = 0
        self.spells = []
        self.book = []

    def addmana(self, amount):
        self.mana += amount

    def addspell(self, spell):
        spell.owner = self.book
        self.book.append(spell)

    def drawspell(self):
        if len(self.spells) >= MAX_SPELLS:
            message('You have no room in your mind for more spells.')
            return
        spell_chances = {}
        for each_spell in self.book:
            spell_chances[each_spell.name] = each_spell.rarity
        new_spell_name = random_choice(spell_chances)
        new_spell = None
        for each_spell in self.book:
            if each_spell.name == new_spell_name:
                new_spell = each_spell
                break
        if new_spell != None:
            new_spell.owner = self.spells
            self.spells.append(new_spell)
            message('You have drawn: ' + new_spell.name)


class Fighter:
    #combat-related properties and methods (monster, player, NPC).
    def __init__(self, hp, defense, power, xp, death_function=None):
        self.max_hp = hp
        self.hp = hp
        self.defense = defense
        self.power = power
        self.xp = xp
        self.death_function = death_function

    def attack(self, target):
        #a simple formula for attack damage
        damage = self.power - target.fighter.defense

        if damage > 0:
            #make the target take some damage
            message(self.owner.name.capitalize() + ' attacks ' + target.name + ' for ' + str(damage) + ' hit points.')
            target.fighter.take_damage(damage)
        else:
            message(self.owner.name.capitalize() + ' attacks ' + target.name + ' but it has no effect!')

    def take_damage(self, damage):
        #apply damage if possible
        if damage > 0:
            self.hp -= damage

            #check for death. if there's a death function, call it
            if self.hp <= 0:
                function = self.death_function
                if function is not None:
                    function(self.owner)

                if self.owner != player:  # yield experience to the player
                    player.fighter.xp += self.xp

    def heal(self, amount):
        #heal by the given amount, without going over the maximum
        self.hp += amount
        if self.hp > self.max_hp:
            self.hp = self.max_hp




class BasicMonster:
    #AI for a basic monster.
    def take_turn(self):
        #a basic monster takes its turn. if you can see it, it can see you
        monster = self.owner
        if libtcod.map_is_in_fov(fov_map, monster.x, monster.y):

            #move towards player if far away
            if monster.distance_to(player) >= 2:
                monster.move_towards(player.x, player.y)

            #close enough, attack! (if the player is still alive.)
            elif player.fighter.hp > 0:
                monster.fighter.attack(player)


class ConfusedMonster:
    #AI for a temporarily confused monster (reverts to previous AI after a while).
    def __init__(self, old_ai, num_turns=CONFUSE_NUM_TURNS):
        self.old_ai = old_ai
        self.num_turns = num_turns

    def take_turn(self):
        if self.num_turns > 0:  # still confused...
            # move in a random direction, and decrease the number of turns confused
            self.owner.move(libtcod.random_get_int(0, -1, 1), libtcod.random_get_int(0, -1, 1))
            self.num_turns -= 1

        else:  # restore the previous AI (this one will be deleted because it's not referenced anymore)
            self.owner.ai = self.old_ai
            message('The ' + self.owner.name + ' is no longer confused!', libtcod.red)


class Item:
    #an item that can be picked up and used.
    def __init__(self, use_function=None):
        self.use_function = use_function

    def pick_up(self):
        #add to the player's inventory and remove from the map
        if len(inventory) >= 26:
            message('Your inventory is full, cannot pick up ' + self.owner.name + '.', libtcod.red)
        else:
            inventory.append(self.owner)
            objects.remove(self.owner)
            message('You picked up a ' + self.owner.name + '!', libtcod.green)

    def drop(self):
        #add to the map and remove from the player's inventory. also, place it at the player's coordinates
        objects.append(self.owner)
        inventory.remove(self.owner)
        self.owner.x = player.x
        self.owner.y = player.y
        message('You dropped a ' + self.owner.name + '.', libtcod.yellow)

    def use(self):
        #just call the "use_function" if it is defined
        if self.use_function is None:
            message('The ' + self.owner.name + ' cannot be used.')
        else:
            if self.use_function() != 'cancelled':
                inventory.remove(self.owner)  # destroy after use, unless it was cancelled for some reason


class Spell:

    def __init__(self, name='Unnamed', cost=0, summon=False, rarity=10, attack=1, life=3, move=1, cast_function=None):
        self.name = name
        self.cost = cost
        self.summon = summon
        self.rarity = rarity
        self.attack = attack
        self.life = life
        self.move = move
        self.cast_function = cast_function
        self.owner = None

    def cast(self):
        # call the "cast_function" if it is defined
        if self.cast_function is None:
            message('The ' + self.owner.name + ' cannot be cast.')
        if self.cost > player.wizard.mana:
            message('That spell costs too much mana to cast.')
        else:
            if self.cast_function() != 'cancelled':
                player.wizard.mana -= self.cost
                self.owner.remove(self)


def is_blocked(x, y):
    #first test the map tile
    if level_map[x][y].blocked:
        return True

    #now check for any blocking objects
    for object in objects:
        if object.blocks and object.x == x and object.y == y:
            return True

    return False


def create_room(room):
    global level_map
    #go through the tiles in the rectangle and make them passable
    for x in range(room.x1 + 1, room.x2):
        for y in range(room.y1 + 1, room.y2):
            level_map[x][y].blocked = False
            level_map[x][y].block_sight = False


def create_h_tunnel(x1, x2, y):
    global level_map
    #horizontal tunnel. min() and max() are used in case x1>x2
    for x in range(min(x1, x2), max(x1, x2) + 1):
        level_map[x][y].blocked = False
        level_map[x][y].block_sight = False


def create_v_tunnel(y1, y2, x):
    global level_map
    #vertical tunnel
    for y in range(min(y1, y2), max(y1, y2) + 1):
        level_map[x][y].blocked = False
        level_map[x][y].block_sight = False


def make_map():
    global level_map, objects, stairs

    #the list of objects with just the player
    objects = [player]

    #fill map with "blocked" tiles
    level_map = [[Tile(True)
        for y in range(MAP_HEIGHT)]
            for x in range(MAP_WIDTH)]

    new_room = Rect(1, 1, MAP_WIDTH - 2, MAP_HEIGHT - 2)
    create_room(new_room)
    place_objects(new_room)
    (new_x, new_y) = new_room.center()
    player.x = new_x - 5
    player.y = new_y - 5

#    rooms = []
#    num_rooms = 0
#
#    for r in range(MAX_ROOMS):
#        #random width and height
#        w = libtcod.random_get_int(0, ROOM_MIN_SIZE, ROOM_MAX_SIZE)
#        h = libtcod.random_get_int(0, ROOM_MIN_SIZE, ROOM_MAX_SIZE)
#        #random position without going out of the boundaries of the map
#        x = libtcod.random_get_int(0, 0, MAP_WIDTH - w - 1)
#        y = libtcod.random_get_int(0, 0, MAP_HEIGHT - h - 1)
#
#        #"Rect" class makes rectangles easier to work with
#        new_room = Rect(x, y, w, h)
#
#        #run through the other rooms and see if they intersect with this one
#        failed = False
#        for other_room in rooms:
#            if new_room.intersect(other_room):
#                failed = True
#                break
#
#        if not failed:
#            #this means there are no intersections, so this room is valid
#
#            #"paint" it to the map's tiles
#            create_room(new_room)
#
#            #add some contents to this room, such as monsters
#            place_objects(new_room)
#
#            #center coordinates of new room, will be useful later
#            (new_x, new_y) = new_room.center()
#
#            if num_rooms == 0:
#                #this is the first room, where the player starts at
#                player.x = new_x
#                player.y = new_y
#            else:
#                #all rooms after the first:
#                #connect it to the previous room with a tunnel
#
#                #center coordinates of previous room
#                (prev_x, prev_y) = rooms[num_rooms - 1].center()
#
#                #draw a coin (random number that is either 0 or 1)
#                if libtcod.random_get_int(0, 0, 1) == 1:
#                    #first move horizontally, then vertically
#                    create_h_tunnel(prev_x, new_x, prev_y)
#                    create_v_tunnel(prev_y, new_y, new_x)
#                else:
#                    #first move vertically, then horizontally
#                    create_v_tunnel(prev_y, new_y, prev_x)
#                    create_h_tunnel(prev_x, new_x, new_y)
#
#            #finally, append the new room to the list
#            rooms.append(new_room)
#            num_rooms += 1
#
    #create stairs at the center of the last room
    stairs = Object(new_x, new_y, '<', 'stairs', libtcod.white, always_visible=True)
    objects.append(stairs)


def random_choice_index(chances):  # choose one option from list of chances, returning its index
    #the dice will land on some number between 1 and the sum of the chances
    dice = libtcod.random_get_int(0, 1, sum(chances))

    #go through all chances, keeping the sum so far
    running_sum = 0
    choice = 0
    for w in chances:
        running_sum += w

        #see if the dice landed in the part that corresponds to this choice
        if dice <= running_sum:
            return choice
        choice += 1


def random_choice(chances_dict):
    #choose one option from dictionary of chances, returning its key
    chances = chances_dict.values()
    strings = chances_dict.keys()

    return strings[random_choice_index(chances)]


def from_dungeon_level(table):
    #returns a value that depends on level. the table specifies what value occurs after each level, default is 0.
    for (value, level) in reversed(table):
        if dungeon_level >= level:
            return value
    return 0


def place_objects(room):
    #this is where we decide the chance of each monster or item appearing.

    #maximum number of monsters per room
    max_monsters = from_dungeon_level([[2, 1], [3, 4], [5, 6]])

    #chance of each monster
    monster_chances = {}
    monster_chances['orc'] = 80  # orc always shows up, even if all other monsters have 0 chance
    monster_chances['troll'] = from_dungeon_level([[15, 3], [30, 5], [60, 7]])

    #maximum number of items per room
    max_items = from_dungeon_level([[1, 1], [2, 4]])

    #chance of each item (by default they have a chance of 0 at level 1, which then goes up)
    item_chances = {}
    item_chances['heal'] = 35  # healing potion always shows up, even if all other items have 0 chance
    item_chances['lightning'] = from_dungeon_level([[25, 4]])
    item_chances['fireball'] = from_dungeon_level([[25, 6]])
    item_chances['confuse'] = from_dungeon_level([[10, 2]])

    #choose random number of monsters
    num_monsters = libtcod.random_get_int(0, 0, max_monsters)

    for i in range(num_monsters):
        #choose random spot for this monster
        x = libtcod.random_get_int(0, room.x1 + 1, room.x2 - 1)
        y = libtcod.random_get_int(0, room.y1 + 1, room.y2 - 1)

        #only place it if the tile is not blocked
        if not is_blocked(x, y):
            choice = random_choice(monster_chances)
            if choice == 'orc':
                #create an orc
                fighter_component = Fighter(hp=20, defense=0, power=4, xp=35, death_function=monster_death)
                ai_component = BasicMonster()

                monster = Object(x, y, 'o', 'orc', libtcod.desaturated_green,
                    blocks=True, fighter=fighter_component, ai=ai_component)

            elif choice == 'troll':
                #create a troll
                fighter_component = Fighter(hp=30, defense=2, power=8, xp=100, death_function=monster_death)
                ai_component = BasicMonster()

                monster = Object(x, y, 'T', 'troll', libtcod.darker_green,
                    blocks=True, fighter=fighter_component, ai=ai_component)

            objects.append(monster)

    #choose random number of items
    num_items = libtcod.random_get_int(0, 0, max_items)

    for i in range(num_items):
        #choose random spot for this item
        x = libtcod.random_get_int(0, room.x1 + 1, room.x2 - 1)
        y = libtcod.random_get_int(0, room.y1 + 1, room.y2 - 1)

        #only place it if the tile is not blocked
        if not is_blocked(x, y):
            choice = random_choice(item_chances)
            if choice == 'heal':
                #create a healing potion
                item_component = Item(use_function=cast_heal)
                item = Object(x, y, '!', 'healing potion', libtcod.violet, item=item_component)

            elif choice == 'lightning':
                #create a lightning bolt scroll
                item_component = Item(use_function=cast_lightning)
                item = Object(x, y, '#', 'scroll of lightning bolt', libtcod.light_yellow, item=item_component)

            elif choice == 'fireball':
                #create a fireball scroll
                item_component = Item(use_function=cast_fireball)
                item = Object(x, y, '#', 'scroll of fireball', libtcod.light_yellow, item=item_component)

            elif choice == 'confuse':
                #create a confuse scroll
                item_component = Item(use_function=cast_confuse)
                item = Object(x, y, '#', 'scroll of confusion', libtcod.light_yellow, item=item_component)

            objects.append(item)
            item.send_to_back()  # items appear below other objects
            item.always_visible = True  # items are visible even out-of-FOV, if in an explored area


def render_bar(x, y, total_width, name, value, maximum, bar_color, back_color):
    #render a bar (HP, experience, etc). first calculate the width of the bar
    bar_width = int(float(value) / maximum * total_width)

    #render the background first
    libtcod.console_set_default_background(panel, back_color)
    libtcod.console_set_background_flag(panel, libtcod.BKGND_SET)
    libtcod.console_rect(panel, x, y, total_width, True, False)

    #now render the bar on top
    libtcod.console_set_default_background(panel, bar_color)
    if bar_width > 0:
        libtcod.console_set_background_flag(panel, libtcod.BKGND_SET)
        libtcod.console_rect(panel, x, y, bar_width, True, False)

    #finally, some centered text with the values
    libtcod.console_set_default_foreground(panel, libtcod.white)
    libtcod.console_print_ex(panel, x + total_width / 2, y, libtcod.BKGND_NONE, libtcod.CENTER,
        name + ': ' + str(value) + '/' + str(maximum))


def get_names_under_mouse():
    #return a string with the names of all objects under the mouse
    mouse = libtcod.mouse_get_status()
    (x, y) = (mouse.cx, mouse.cy)

    #create a list with the names of all objects at the mouse's coordinates and in FOV
    names = [obj.name + ' ' + str(obj.fighter.hp) + '/' + str(obj.fighter.max_hp) for obj in objects
        if obj.x == x and obj.y == y and libtcod.map_is_in_fov(fov_map, obj.x, obj.y) and obj.fighter != None]

    names = ', '.join(names)  # join the names, separated by commas
    return names.capitalize()


def render_all():
    global fov_map, color_dark_wall, color_light_wall
    global color_dark_ground, color_light_ground
    global fov_recompute

    if fov_recompute:
        #recompute FOV if needed (the player moved or something)
        fov_recompute = False
        libtcod.map_compute_fov(fov_map, player.x, player.y, TORCH_RADIUS, FOV_LIGHT_WALLS, FOV_ALGO)

        #go through all tiles, and set their background color according to the FOV
        for y in range(MAP_HEIGHT):
            for x in range(MAP_WIDTH):
                visible = libtcod.map_is_in_fov(fov_map, x, y)
                wall = level_map[x][y].block_sight
                if not visible:
                    #if it's not visible right now, the player can only see it if it's explored
                    if level_map[x][y].explored:
                        if wall:
                            libtcod.console_set_char_background(con, x, y, color_dark_wall, libtcod.BKGND_SET)
                        else:
                            libtcod.console_set_char_background(con, x, y, color_dark_ground, libtcod.BKGND_SET)
                else:
                    #it's visible
                    if wall:
                        libtcod.console_set_char_background(con, x, y, color_light_wall, libtcod.BKGND_SET)
                    else:
                        libtcod.console_set_char_background(con, x, y, color_light_ground, libtcod.BKGND_SET)
                    #since it's visible, explore it
                    level_map[x][y].explored = True
                    mana = level_map[x][y].mana

                    if mana > 0:
                        libtcod.console_set_default_foreground(con, libtcod.white)
                        libtcod.console_print(con, x, y, str(level_map[x][y].mana))

    #draw all objects in the list, except the player. we want it to
    #always appear over all other objects! so it's drawn later.
    for object in objects:
        if object != player:
            object.draw()
    player.draw()

    #blit the contents of "con" to the root console
    libtcod.console_blit(con, 0, 0, MAP_WIDTH, MAP_HEIGHT, 0, 0, 0)

    #prepare to render the GUI panel
    libtcod.console_set_default_background(panel, libtcod.black)
    libtcod.console_clear(panel)
    libtcod.console_clear(msg)

    #print the game messages, one line at a time
    y = 1
    for (line, color) in game_msgs:
        libtcod.console_set_default_foreground(msg, color)
        libtcod.console_print_ex(msg, 1, y, libtcod.BKGND_NONE, libtcod.LEFT, line)
        y += 1

    #show the player's stats
    render_bar(1, 1, BAR_WIDTH, 'HP', player.fighter.hp, player.fighter.max_hp,
        libtcod.light_red, libtcod.darker_red)
    render_bar(1, 2, BAR_WIDTH, 'Mana', player.wizard.mana, 100,
        libtcod.light_blue, libtcod.darker_blue)
    libtcod.console_print_ex(panel, 1, 3, libtcod.BKGND_NONE, libtcod.LEFT, 'Dungeon level ' + str(dungeon_level))

    #display names of objects under the mouse
    libtcod.console_set_default_foreground(panel, libtcod.light_gray)
    libtcod.console_print_ex(panel, 25, 0, libtcod.BKGND_NONE, libtcod.LEFT, get_names_under_mouse())

    #blit the contents of "panel" to the root console
    libtcod.console_blit(panel, 0, 0, SCREEN_WIDTH, PANEL_HEIGHT, 0, 0, PANEL_Y)

    libtcod.console_blit(msg, 0, 0, MSG_WIDTH, MSG_HEIGHT, 0, MAP_WIDTH, 0)


def message(new_msg, color=libtcod.white):
    #split the message if necessary, among multiple lines
    new_msg_lines = textwrap.wrap(new_msg, MSG_WIDTH - 1)

    for line in new_msg_lines:
        #if the buffer is full, remove the first line to make room for the new one
        if len(game_msgs) == MSG_HEIGHT:
            del game_msgs[0]

        #add the new line as a tuple, with the text and the color
        game_msgs.append((line, color))


def player_move_or_attack(dx, dy):
    global fov_recompute

    #the coordinates the player is moving to/attacking
    x = player.x + dx
    y = player.y + dy

    #try to find an attackable object there
    target = None
    for object in objects:
        if object.fighter and object.x == x and object.y == y:
            target = object
            break

    #attack if target found, move otherwise
    if target is not None:
        player.fighter.attack(target)
    else:
        player.move(dx, dy)
        if level_map[x][y].mana > 0:
            player.wizard.addmana(level_map[x][y].mana)
            level_map[x][y].mana = 0
        fov_recompute = True


def menu(header, options, width):
    if len(options) > 26:
        raise ValueError('Cannot have a menu with more than 26 options.')

    #calculate total height for the header (after auto-wrap) and one line per option
    header_height = libtcod.console_get_height_rect(con, 0, 0, width, SCREEN_HEIGHT, header)
    if header == '':
        header_height = 0
    height = len(options) + header_height

    #create an off-screen console that represents the menu's window
    window = libtcod.console_new(width, height)

    #print the header, with auto-wrap
    libtcod.console_set_default_foreground(window, libtcod.white)
    libtcod.console_print_rect(window, 0, 0, width, height, header)

    #print all the options
    y = header_height
    letter_index = ord('a')
    for option_text in options:
        text = '(' + chr(letter_index) + ') ' + option_text
        libtcod.console_print_ex(window, 0, y, libtcod.BKGND_NONE, libtcod.LEFT, text)
        y += 1
        letter_index += 1

    #blit the contents of "window" to the root console
    x = SCREEN_WIDTH / 2 - width / 2
    y = SCREEN_HEIGHT / 2 - height / 2
    libtcod.console_blit(window, 0, 0, width, height, 0, x, y, 1.0, 0.7)

    #present the root console to the player and wait for a key-press
    libtcod.console_flush()
    key = libtcod.console_wait_for_keypress(True)

    if key.vk == libtcod.KEY_ENTER and key.lalt:  # (special case) Alt+Enter: toggle fullscreen
        libtcod.console_set_fullscreen(not libtcod.console_is_fullscreen())

    #convert the ASCII code to an index; if it corresponds to an option, return it
    index = key.c - ord('a')
    if index >= 0 and index < len(options):
        return index
    return None


def inventory_menu(header):
    #show a menu with each item of the inventory as an option
    if len(inventory) == 0:
        options = ['Inventory is empty.']
    else:
        options = [item.name for item in inventory]

    index = menu(header, options, INVENTORY_WIDTH)

    #if an item was chosen, return it
    if index is None or len(inventory) == 0:
        return None
    return inventory[index].item


def spell_menu(header):
    #show a menu with each item of the inventory as an option
    if len(player.wizard.spells) == 0:
        options = ['Spell list is empty.']
    else:
        options = [spell.name for spell in player.wizard.spells]

    index = menu(header, options, INVENTORY_WIDTH)

    #if an item was chosen, return it
    if index is None or len(player.wizard.spells) == 0:
        return None
    return player.wizard.spells[index]


def msgbox(text, width=50):
    menu(text, [], width)  # use menu() as a sort of "message box"


def handle_keys():
    key = libtcod.console_check_for_keypress(libtcod.KEY_PRESSED)

    if key.vk == libtcod.KEY_ENTER and key.lalt:
        #Alt+Enter: toggle fullscreen
        libtcod.console_set_fullscreen(not libtcod.console_is_fullscreen())

    elif key.vk == libtcod.KEY_ESCAPE:
        return 'exit'  # exit game

    if game_state == 'playing':
        #movement keys
        if key.vk in [libtcod.KEY_UP, libtcod.KEY_KP8]:
            player_move_or_attack(0, -1)

        elif key.vk in [libtcod.KEY_DOWN, libtcod.KEY_KP2]:
            player_move_or_attack(0, 1)

        elif key.vk in [libtcod.KEY_LEFT, libtcod.KEY_KP4]:
            player_move_or_attack(-1, 0)

        elif key.vk in [libtcod.KEY_RIGHT, libtcod.KEY_KP6]:
            player_move_or_attack(1, 0)

        elif key.vk == libtcod.KEY_KP7:
            player_move_or_attack(-1, -1)

        elif key.vk == libtcod.KEY_KP9:
            player_move_or_attack(1, -1)

        elif key.vk == libtcod.KEY_KP1:
            player_move_or_attack(-1, 1)

        elif key.vk == libtcod.KEY_KP3:
            player_move_or_attack(1, 1)

        else:
            #test for other keys
            key_char = chr(key.c)

            if key_char == 'g':
                #pick up an item
                for object in objects:  # look for an item in the player's tile
                    if object.x == player.x and object.y == player.y and object.item:
                        object.item.pick_up()
                        break

            if key_char == 'i':
                #show the inventory; if an item is selected, use it
                chosen_item = inventory_menu('Press the key next to an item to use it, or any other to cancel.\n')
                if chosen_item is not None:
                    chosen_item.use()

            if key_char == 's':
                #show the spell menu; if a spell is selected, use it
                chosen_spell = spell_menu('Press the key next to a spell to use it, or any other to cancel.\n')
                if chosen_spell is not None:
                    chosen_spell.cast()

            if key_char == 'd':
                # draw a spell at random from book
                player.wizard.drawspell()
                return 'turn-taken'

            if key_char == 'c':
                #show character information
                level_up_xp = LEVEL_UP_BASE + player.level * LEVEL_UP_FACTOR
                msgbox('Character Information\n\nLevel: ' + str(player.level) + '\nExperience: ' + str(player.fighter.xp) +
                    '\nExperience to level up: ' + str(level_up_xp) + '\n\nMaximum HP: ' + str(player.fighter.max_hp) +
                    '\nAttack: ' + str(player.fighter.power) + '\nDefense: ' + str(player.fighter.defense), CHARACTER_SCREEN_WIDTH)

            if key_char == '<':
                #go down stairs, if the player is on them
                if stairs.x == player.x and stairs.y == player.y:
                    next_level()

            return 'didnt-take-turn'


def check_level_up():
    #see if the player's experience is enough to level-up
    level_up_xp = LEVEL_UP_BASE + player.level * LEVEL_UP_FACTOR
    if player.fighter.xp >= level_up_xp:
        #it is! level up and ask to raise some stats
        player.level += 1
        player.fighter.xp -= level_up_xp
        message('Your battle skills grow stronger! You reached level ' + str(player.level) + '!', libtcod.yellow)

        choice = None
        while choice == None:  # keep asking until a choice is made
            choice = menu('Level up! Choose a stat to raise:\n',
                ['Constitution (+20 HP, from ' + str(player.fighter.max_hp) + ')',
                'Strength (+1 attack, from ' + str(player.fighter.power) + ')',
                'Agility (+1 defense, from ' + str(player.fighter.defense) + ')'], LEVEL_SCREEN_WIDTH)

        if choice == 0:
            player.fighter.max_hp += 20
            player.fighter.hp += 20
        elif choice == 1:
            player.fighter.power += 1
        elif choice == 2:
            player.fighter.defense += 1


def player_death(player):
    #the game ended!
    global game_state
    message('You died!', libtcod.red)
    game_state = 'dead'

    #for added effect, transform the player into a corpse!
    player.char = '%'
    player.color = libtcod.dark_red


def monster_death(monster):
    #transform it into a nasty corpse! it doesn't block, can't be
    #attacked and doesn't move
    message('The ' + monster.name + ' is dead! You gain ' + str(monster.fighter.xp) + ' experience points.', libtcod.orange)
    monster.char = '%'
    monster.color = libtcod.dark_red
    monster.blocks = False
    monster.fighter = None
    monster.ai = None
    monster.name = 'remains of ' + monster.name
    monster.send_to_back()


def target_tile(max_range=None):
    #return the position of a tile left-clicked in player's FOV (optionally in a range), or (None,None) if right-clicked.
    while True:
        #render the screen. this erases the inventory and shows the names of objects under the mouse.
        render_all()
        libtcod.console_flush()

        key = libtcod.console_check_for_keypress()
        mouse = libtcod.mouse_get_status()  # get mouse position and click status
        (x, y) = (mouse.cx, mouse.cy)

        if mouse.rbutton_pressed or key.vk == libtcod.KEY_ESCAPE:
            return (None, None)  # cancel if the player right-clicked or pressed Escape

        #accept the target if the player clicked in FOV, and in case a range is specified, if it's in that range
        if (mouse.lbutton_pressed and libtcod.map_is_in_fov(fov_map, x, y) and
            (max_range is None or player.distance(x, y) <= max_range)):
            return (x, y)


def target_monster(max_range=None):
    #returns a clicked monster inside FOV up to a range, or None if right-clicked
    while True:
        (x, y) = target_tile(max_range)
        if x is None:  # player cancelled
            return None

        #return the first clicked monster, otherwise continue looping
        for obj in objects:
            if obj.x == x and obj.y == y and obj.fighter and obj != player:
                return obj


def closest_monster(max_range):
    #find closest enemy, up to a maximum range, and in the player's FOV
    closest_enemy = None
    closest_dist = max_range + 1  # start with (slightly more than) maximum range

    for object in objects:
        if object.fighter and not object == player and libtcod.map_is_in_fov(fov_map, object.x, object.y):
            #calculate distance between this object and the player
            dist = player.distance_to(object)
            if dist < closest_dist:  # it's closer, so remember it
                closest_enemy = object
                closest_dist = dist
    return closest_enemy


def cast_heal():
    #heal the player
    if player.fighter.hp == player.fighter.max_hp:
        message('You are already at full health.', libtcod.red)
        return 'cancelled'

    message('Your wounds start to feel better!', libtcod.light_violet)
    player.fighter.heal(HEAL_AMOUNT)


def cast_lightning():
    #find closest enemy (inside a maximum range) and damage it
    monster = closest_monster(LIGHTNING_RANGE)
    if monster is None:  # no enemy found within maximum range
        message('No enemy is close enough to strike.', libtcod.red)
        return 'cancelled'

    #zap it!
    message('A lighting bolt strikes the ' + monster.name + ' with a loud thunder! The damage is '
        + str(LIGHTNING_DAMAGE) + ' hit points.', libtcod.light_blue)
    monster.fighter.take_damage(LIGHTNING_DAMAGE)


def cast_fireball():
    #ask the player for a target tile to throw a fireball at
    message('Left-click a target tile for the fireball, or right-click to cancel.', libtcod.light_cyan)
    (x, y) = target_tile()
    if x is None:
        return 'cancelled'
    message('The fireball explodes, burning everything within ' + str(FIREBALL_RADIUS) + ' tiles!', libtcod.orange)

    for obj in objects:  # damage every fighter in range, including the player
        if obj.distance(x, y) <= FIREBALL_RADIUS and obj.fighter:
            message('The ' + obj.name + ' gets burned for ' + str(FIREBALL_DAMAGE) + ' hit points.', libtcod.orange)
            obj.fighter.take_damage(FIREBALL_DAMAGE)


def cast_confuse():
    #ask the player for a target to confuse
    message('Left-click an enemy to confuse it, or right-click to cancel.', libtcod.light_cyan)
    monster = target_monster(CONFUSE_RANGE)
    if monster is None:
        return 'cancelled'

    #replace the monster's AI with a "confused" one; after some turns it will restore the old AI
    old_ai = monster.ai
    monster.ai = ConfusedMonster(old_ai)
    monster.ai.owner = monster  # tell the new component who owns it
    message('The eyes of the ' + monster.name + ' look vacant, as he starts to stumble around!', libtcod.light_green)


def save_game():
    #open a new empty shelve (possibly overwriting an old one) to write the game data
    file_handle = shelve.open('savegame', 'n')
    file_handle['level_map'] = level_map
    file_handle['objects'] = objects
    file_handle['player_index'] = objects.index(player)  # index of player in objects list
    file_handle['stairs_index'] = objects.index(stairs)  # same for the stairs
    file_handle['inventory'] = inventory
    file_handle['game_msgs'] = game_msgs
    file_handle['game_state'] = game_state
    file_handle['dungeon_level'] = dungeon_level
    file_handle.close()


def load_game():
    #open the previously saved shelve and load the game data
    global level_map, objects, player, stairs, inventory, game_msgs, game_state, dungeon_level

    file_handle = shelve.open('savegame', 'r')
    level_map = file_handle['level_map']
    objects = file_handle['objects']
    player = objects[file_handle['player_index']]  # get index of player in objects list and access it
    stairs = objects[file_handle['stairs_index']]  # same for the stairs
    inventory = file_handle['inventory']
    game_msgs = file_handle['game_msgs']
    game_state = file_handle['game_state']
    dungeon_level = file_handle['dungeon_level']
    file_handle.close()

    initialize_fov()


def new_game():
    global player, inventory, game_msgs, game_state, dungeon_level

    #create object representing the player
    fighter_component = Fighter(hp=100, defense=1, power=4, xp=0, death_function=player_death)
    wizard_component = Wizard()
    player = Object(0, 0, '@', 'player', libtcod.yellow, blocks=True, fighter=fighter_component, wizard=wizard_component)

    player.level = 1

    #generate map (at this point it's not drawn to the screen)
    dungeon_level = 1
    make_map()
    initialize_fov()

    game_state = 'playing'
    inventory = []

    fireball = Spell(name='Fireball', cost=5, summon=False, cast_function=cast_fireball, rarity=5)
    player.wizard.addspell(fireball)

    lightning = Spell(name='Spark', cost=2, summon=False, cast_function=cast_lightning, rarity=10)
    player.wizard.addspell(lightning)

    heal = Spell(name='Self Heal', cost=3, summon=False, cast_function=cast_heal, rarity=10)
    player.wizard.addspell(heal)

    generate_mana(20)

    #create the list of game messages and their colors, starts empty
    game_msgs = []

    #a warm welcoming message!
    message('Early alpha version.', libtcod.red)


def next_level():
    #advance to the next level
    global dungeon_level
    message('You take a moment to rest, and recover your strength.', libtcod.light_violet)
    player.fighter.heal(player.fighter.max_hp / 2)  # heal the player by 50%

    dungeon_level += 1
    message('After a rare moment of peace, you descend deeper into the heart of the dungeon...', libtcod.red)
    make_map()  # create a fresh new level!
    initialize_fov()


def initialize_fov():
    global fov_recompute, fov_map
    fov_recompute = True

    #create the FOV map, according to the generated map
    fov_map = libtcod.map_new(MAP_WIDTH, MAP_HEIGHT)
    for y in range(MAP_HEIGHT):
        for x in range(MAP_WIDTH):
            libtcod.map_set_properties(fov_map, x, y, not level_map[x][y].block_sight, not level_map[x][y].blocked)

    libtcod.console_clear(con)  # unexplored areas start black (which is the default background color)


def play_game():
    global fov_recompute
    player_action = None

    while not libtcod.console_is_window_closed():
        # render the screen
        render_all()

        libtcod.console_flush()

        # level up if needed
        check_level_up()

        # erase all objects at their old locations, before they move
        for object in objects:
            object.clear()

        # handle keys and exit game if needed
        player_action = handle_keys()
        if player_action == 'exit':
            save_game()
            break

        # let monsters take their turn
        if game_state == 'playing' and player_action != 'didnt-take-turn':
            for object in objects:
                if object.ai:
                    object.ai.take_turn()

            generate_mana(5)
            fov_recompute = True


def generate_mana(amount):
    for i in range(0, amount):
        x = libtcod.random_get_int(0, 2, MAP_WIDTH - 2)
        y = libtcod.random_get_int(0, 2, MAP_HEIGHT - 2)
        level_map[x][y].mana += libtcod.random_get_int(0, 1, 3)


def main_menu():
    img = libtcod.image_load('title.png')

    while not libtcod.console_is_window_closed():
        # show the background image, at twice the regular console resolution
        libtcod.image_blit_2x(img, 0, 0, 0)

        # show the game's title, and some credits!
        libtcod.console_set_default_foreground(0, libtcod.black)
        libtcod.console_print_ex(0, SCREEN_WIDTH / 2, int(SCREEN_HEIGHT * .75), libtcod.BKGND_NONE, libtcod.CENTER, 'MagicRL')
        libtcod.console_print_ex(0, SCREEN_WIDTH / 2, int(SCREEN_HEIGHT * .75) + 2, libtcod.BKGND_NONE, libtcod.CENTER, 'By Dragyn')

        # show options and wait for the player's choice
        choice = menu('', ['Play a new game', 'Continue last game', 'Quit'], 24)

        if choice == 0:  # new game
            libtcod.console_clear(0)
            new_game()
            play_game()
        if choice == 1:  # load last game
            try:
                load_game()
            except:
                msgbox('\n No saved game to load.\n', 24)
                continue
            play_game()
        elif choice == 2:  # quit
            break

#libtcod.console_set_custom_font('arial10x10.png', libtcod.FONT_TYPE_GREYSCALE | libtcod.FONT_LAYOUT_TCOD)
libtcod.console_set_custom_font('magicrl_tileset_32x32.png', libtcod.FONT_TYPE_GREYSCALE | libtcod.FONT_LAYOUT_ASCII_INROW,
    nb_char_horiz=16, nb_char_vertic=32)
libtcod.console_init_root(SCREEN_WIDTH, SCREEN_HEIGHT, 'python/libtcod tutorial', False)
libtcod.sys_set_fps(LIMIT_FPS)
con = libtcod.console_new(MAP_WIDTH, MAP_HEIGHT)
panel = libtcod.console_new(SCREEN_WIDTH, PANEL_HEIGHT)
msg = libtcod.console_new(MSG_WIDTH, MSG_HEIGHT)
main_menu()
