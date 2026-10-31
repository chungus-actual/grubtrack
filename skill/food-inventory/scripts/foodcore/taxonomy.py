"""The food taxonomy: 12 departments, ~90 categories, and the generic nouns that place a name.

`classify()` answers three questions about any name (voice log, receipt line, shopping-list
text): which department and category it belongs to, and what *generic* thing it is
("eggs", "kefir", "chicken breast"). The generic is what makes prices comparable across brands
and stores; the category is what the pantry and the spend charts group by.

How a name is placed, in order:
  1. phrase match against GENERICS (brands are phrases too). Longest phrase wins and
     *consumes* its words, so "chocolate chip" can't make waffles a bag of chips; among the
     survivors the higher tier wins (a dish beats a product form beats a raw ingredient),
     then the later word (English puts the head noun last: "chicken pesto SANDWICH").
  2. context adjusts: '(prepared)' / ready-meal makes a meal of it by storage; frozen produce
     moves to Frozen.
  3. no phrase matched: the free-form legacy `category`/`subCategory`/`spiritType` the agent
     wrote, then the receipt section, then `other.other`.

Never invent: a name that matches nothing lands in `<dept>.other` with confidence "low", and
GrubTrack lists those for a person to place. A person's choice is stored on the product with
catSource='user' and is never overwritten by this function.
"""

from __future__ import annotations

from .text import expand_receipt, parens, tokens

# key, label, chart-order. Order is the walk through a store, which is how people scan.
DEPARTMENTS = [
    ("produce", "Produce"),
    ("meat", "Meat & Seafood"),
    ("deli", "Deli & Prepared"),
    ("dairy", "Dairy & Eggs"),
    ("bakery", "Bakery"),
    ("frozen", "Frozen"),
    ("pantry", "Staples"),
    ("snacks", "Snacks & Sweets"),
    ("drinks", "Drinks"),
    ("bar", "Bar"),
    ("household", "Household & Health"),
    ("other", "Other"),
]
DEPT_LABEL = dict(DEPARTMENTS)

# Category blocks: [dept.cat | Label | tier] followed by `;`-separated entries.
# An entry is `generic = synonym, synonym` (the generic is itself a synonym).
# Tier: 3 = a dish / prepared thing, 2 = a product form, 1 = a raw ingredient, 0 = modifier
# (consumes words but never classifies: "chocolate chip", "gluten free").
_TABLE = r"""
[produce.fruit | Fruit | 1]
apple = apples cosmic crisp, cosmic crisp, honeycrisp, gala, fuji, granny smith; banana; grape = seedless grape, scarlotta grape, red grape, green grape;
lemon; lime; orange = navel orange, cara cara, blood orange; mandarin = clementine, cutie, tangerine, satsuma;
pear; peach; plum; nectarine; cherry; strawberry; blueberry; raspberry; blackberry; berry = mixed berry;
mango; pineapple; kiwi; melon; watermelon; cantaloupe; honeydew; avocado; pomegranate; fig; grapefruit;
papaya; persimmon; apricot; cranberry; date = medjool date; dragon fruit; passion fruit; plantain; coconut

[produce.vegetables | Vegetables | 1]
broccoli = broccoli floret, signature broccoli floret; cauliflower; carrot = baby carrot, baby cut carrot, organic carrot;
celery = celery heart, celery rib, celery stalk; cucumber = mini cucumber, persian cucumber, english cucumber; tomato = cherry tomato, grape tomato,
snacking tomato, roma tomato, heirloom tomato, glorys, glory; bell pepper = red pepper, green pepper, sweet pepper, mini pepper;
pepper = anaheim pepper, anaheim; green chili = hatch chili, hatch green chili, green chile, roasted green chili; jalapeno = jalapeno pepper; poblano; serrano; habanero; zucchini; squash = butternut squash, acorn squash, spaghetti squash;
eggplant; potato = russet, yukon gold, baby potato, red potato; sweet potato = yam; corn = corn on the cob;
green bean = string bean, haricot vert, french bean; snap pea = sugar snap pea, snow pea; pea; asparagus; brussels sprout;
cabbage = red cabbage, green cabbage, savoy cabbage, napa cabbage; bok choy = baby bok choy; mushroom = cremini,
shiitake, portobello, button mushroom, oyster mushroom; beet; radish = daikon; turnip; parsnip; okra; artichoke; leek; fennel;
bean sprout = mung bean sprout, sprout; edamame; jicama; tomatillo; mixed vegetables = vegetable, veggie, vegetable medley, veggie blend, stir fry vegetable, stir fry veggie blend, stir fry fresh vegetable, veggie variety, forest vegetable medley

[produce.greens | Greens & Salad | 2]
spinach = baby spinach, earthbound spinach; lettuce = romaine, iceberg, butter lettuce, little gem, romaine heart;
arugula; kale = baby kale, lacinato; spring mix = mixed green, mesclun, salad mix, power green, super green;
chard = swiss chard; collard = collard green; microgreen; watercress

[produce.saladkits | Salad Kits | 3]
salad kit = salad complete, chopped salad kit, caesar kit, caesar salad kit, bag salad, bagged salad, chopped salad, crunch salad,
salad blend, chopped salad blend

[produce.herbs | Fresh Herbs | 1]
parsley = italian parsley, flat leaf parsley; cilantro; basil = thai basil; mint; dill; rosemary; thyme; sage; chive;
tarragon; fresh oregano

[produce.aromatics | Onions, Garlic & Ginger | 1]
onion = yellow onion, red onion, white onion, sweet onion; green onion = scallion, spring onion, onions green;
shallot = onions shallot; garlic = garlic bulk, garlic bulb, head of garlic; ginger = ginger root; lemongrass;
ginger paste; garlic paste; lemongrass paste = lemongrass blend; herb paste = gourmet garden; chili = chile, thai chili

[meat.poultry | Poultry | 1]
chicken; chicken breast = boneless skinless chicken breast, chicken breast boneless skinless, chicken lemon,
lemon chicken, citrus lemon chicken, bourbon chicken; chicken thigh; chicken wing = wing; drumstick; whole chicken;
ground chicken; turkey; turkey breast; ground turkey; duck

[meat.beef | Beef | 1]
beef; ground beef = lean ground beef, 80% lean ground beef, hamburger; steak = ribeye, new york strip, filet, tri tip,
flank steak, skirt steak; sirloin; brisket; chuck roast = pot roast, chuck; short rib; stew meat; veal; carne asada = carne asada flat meat, flap meat

[meat.pork | Pork | 1]
pork; pork chop; pork loin = pork tenderloin; pork shoulder = pork butt, carnitas; rib = baby back rib, spare rib; lamb

[meat.sausage | Sausage & Bacon | 1]
sausage = italian sausage, breakfast sausage, chicken sausage; bacon = turkey bacon; chorizo; hot dog = frank, frankfurter;
bratwurst = brat; kielbasa; pepperoni = uncured pepperoni

[meat.seafood | Fish & Seafood | 1]
salmon; tuna steak = ahi; shrimp = prawn; cod; tilapia; fish; scallop; crab; lobster; trout; halibut; mahi = mahi mahi;
mussel; clam; sea bass; swordfish

[meat.plant | Tofu & Plant Protein | 2]
tofu = extra firm tofu, firm tofu, silken tofu, extra-firm tofu; tempeh; seitan; plant based meat = beyond, impossible

[deli.meats | Deli Meats | 2]
deli turkey = sliced turkey, sliced turkey breast, roasted turkey, sliced roasted turkey, oven roasted turkey;
deli chicken = sliced chicken, sliced chicken breast, oven roasted chicken breast; roast beef; ham = deli ham, honey ham; salami = genoa salami;
prosciutto; deli meat = lunch meat, cold cut, sandwich meat, deli slice, breast slice, turkey breast slice, chicken breast slice; pastrami; mortadella; capicola; bologna

[deli.meals | Prepared Meals | 3]
prepared meal = prepared, ready meal, ready to eat; bowl = grain bowl, rice bowl, burrito bowl, poke bowl;
sandwich = sub, hoagie, panini, uncrustable; wrap; burrito = breakfast burrito; meal kit = pizza meal kit;
rotisserie chicken; sushi; entree; pizza; calzone; quesadilla; taco kit; teriyaki chicken = bbq teriyaki chicken

[deli.salads | Prepared Salads | 3]
salad = beet salad, better beet salad, greek salad, caesar salad; potato salad; pasta salad; chicken salad; egg salad;
tuna salad; coleslaw = slaw

[deli.dips | Dips & Spreads | 2]
tapenade = olive tapenade;
hummus = organic original hummus; guacamole = guac; dip = spinach dip, queso dip, onion dip; tzatziki; pico de gallo = salsa fresca;
pimento cheese; baba ganoush

[dairy.milk | Milk | 2]
milk = 2% milk, whole milk, skim milk, nonfat milk, 1% milk, lowfat milk, lactose free milk, ultra filtered milk,
fairlife, fairlife milk, fairlife 2% milk; chocolate milk; buttermilk

[dairy.plantmilk | Plant Milk | 2]
oat milk = oatly, oatly oat milk, oat beverage, matcha oat milk; almond milk = almond beverage, almond non dairy beverage; soy milk; cashew milk; pea milk; banana milk; pistachio milk;
coconut milk beverage; plant milk = non dairy beverage, nondairy beverage, plant based milk

[dairy.yogurt | Yogurt & Kefir | 2]
yogurt = greek yogurt, siggis yogurt, plain yogurt, vanilla yogurt; skyr = siggis, siggis skyr; kefir = lifeway, lifeway kefir,
raspberry kefir, lifeway kefir mixed; drinkable yogurt

[dairy.cheese | Cheese | 2]
cheese = shredded cheese, sliced cheese, block cheese, quattro formaggi; string cheese = cheese string, lucerne string cheese,
cheese stick; cheddar; mozzarella = fresh mozzarella, burrata; parmesan = parmigiano, parm, grated parmesan; swiss = lacy swiss,
baby swiss; havarti = kretschmar havarti; feta; goat cheese = chevre; brie; cream cheese = whipped cream cheese, cheese spread, cheeseball;
mascarpone; jarlsberg; skim milk mozzarella; cottage cheese; ricotta;
provolone; pepper jack; gouda; manchego; halloumi; queso fresco = cotija; blue cheese = gorgonzola; muenster

[dairy.eggs | Eggs | 1]
eggs = egg, dozen egg, large egg, cage free egg, pete and gerry egg, pasture raised egg; egg white = liquid egg white,
liquid egg

[dairy.butter | Butter & Cream | 2]
butter = salted butter, unsalted butter; heavy cream = whipping cream, heavy whipping cream; sour cream;
half and half; ghee; creme fraiche; whipped cream = whipped topping

[dairy.creamer | Creamer | 2]
creamer = coffee creamer, oat creamer, brown sugar oat creamer

[bakery.bread | Bread | 2]
bread = sandwich bread, whole wheat bread, sourdough, rye bread, multigrain bread; baguette; bagel; english muffin;
bun = hamburger bun, hot dog bun, brioche bun; roll = dinner roll, hoagie roll; ciabatta; brioche; focaccia; pizza dough = dough

[bakery.tortillas | Tortillas & Flatbreads | 2]
tortilla = flour tortilla, corn tortilla, ready to cook tortilla, tortillaland, uncooked tortilla; pita; naan; flatbread;
lavash; taco shell

[bakery.pastry | Pastry & Cake | 2]
croissant = honey croissant; muffin; donut = doughnut; pastry; cake; pie; danish; scone; cupcake; brownie; cinnamon roll; babka; crumpet;
cookie dough

[frozen.meals | Frozen Meals | 3]
frozen meal = frozen entree, tv dinner; frozen pizza; pot pie; frozen burrito; frozen lasagna

[frozen.breakfast | Frozen Breakfast | 2]
waffle = toaster waffle, frozen waffle, power waffle, protein waffle, kodiak waffle, blu toaster waffle, blueberry waffle;
pancake = frozen pancake; breakfast sandwich; french toast stick

[frozen.vegetables | Frozen Vegetables | 2]
frozen vegetable = frozen veggie, frozen mixed vegetable; frozen pea; frozen corn; frozen broccoli; frozen spinach;
riced cauliflower; stir fry vegetable

[frozen.fruit | Frozen Fruit | 2]
frozen fruit = frozen berry, frozen mango, frozen strawberry, frozen blueberry, acai pack

[frozen.apps | Dumplings & Appetizers | 2]
okonomiyaki = japanese vegetable pancake, vegetable pancake;
dumpling = potsticker, gyoza, bao, soup dumpling; egg roll = spring roll; frozen appetizer = pizza roll, taquito,
mozzarella stick, chicken nugget, nugget, tender

[frozen.desserts | Ice Cream & Frozen Treats | 2]
ice cream = gelato, pint; sorbet; popsicle = ice pop, fruit bar; frozen yogurt; mochi

[pantry.grains | Rice & Grains | 2]
rice = white rice, long grain rice; jasmine rice = signature jasmine rice, uncle bens jasmine rice; basmati rice;
brown rice; microwave rice = ready rice, rice cup, bens original, ben original, uncle ben; quinoa; farro = pearled farro;
couscous; barley; bulgur; grain blend = seeds of change, seed of change, quinoa and brown rice; wild rice; polenta; grits

[pantry.pasta | Pasta & Noodles | 2]
pasta = spaghetti, penne, rigatoni, fusilli, linguine, fettuccine, rotini, farfalle, orzo, macaroni, shells, bucatini;
noodle = ramen noodle, udon, soba, rice noodle, egg noodle, lo mein, glass noodle; lasagna noodle; gnocchi; ravioli = tortellini

[pantry.canned | Canned Goods | 2]
canned chili = fire roasted green chili, fire roasted diced green chili, diced green chili;
soup = french onion soup, campbell, campbells, chicken noodle soup, tomato soup, campbell soup; canned tomato = diced tomato,
crushed tomato, whole peeled tomato, tomato puree, canned tomatoes; canned tuna = tuna; canned salmon; sardine; anchovy;
canned fruit = mandarin orange cup, fruit cup, pineapple chunk, packed in juice, pineapple slice packed in juice; canned meat = spam, canned chicken; chili = canned chili;
coconut milk = coconut cream; canned corn; canned vegetable

[pantry.beans | Beans & Lentils | 2]
bean = black bean, pinto bean, kidney bean, cannellini, white bean, refried bean, baked bean; chickpea = garbanzo;
lentil = red lentil; split pea

[pantry.broth | Broth & Stock | 2]
broth = stock, chicken broth, beef broth, vegetable broth, bone broth, organic chicken broth, organic beef broth,
tinga style chicken broth; bouillon = better than bouillon, broth base, stock cube, bouillon cube, roast beef base, beef base, chicken base,
vegetable base

[pantry.sauces | Sauces & Pastes | 2]
pasta sauce = marinara, marinara sauce, tomato basil sauce, vodka sauce, alfredo, michaels of brooklyn; pesto = pesto sauce, basil pesto, basil pesto sauce;
curry paste = red curry paste, green curry paste; curry = curry mix, curry roux, japanese curry, golden curry;
simmer sauce = tinga, tikka masala sauce, enchilada sauce, mole, cooking sauce; tomato paste; tomato sauce; pizza sauce; teriyaki = teriyaki sauce;
stir fry sauce; fish sauce; oyster sauce; hoisin; gochujang; chili crisp = chili oil; sauce

[pantry.condiments | Condiments & Dressings | 2]
ketchup; mustard = dijon, yellow mustard, whole grain mustard, grey poupon, pommery; mayo = mayonnaise, kewpie; hot sauce = sriracha, tabasco,
salsa brava, la victoria salsa brava, valentina, cholula; horseradish = bubbies horseradish, prepared horseradish;
relish; salsa = salsa verde, jarred salsa; bbq sauce = barbecue sauce; soy sauce = tamari, shoyu; dressing = salad dressing,
ranch, vinaigrette, caesar dressing, italian dressing; aioli; worcestershire; honey mustard; sweet chili sauce; tahini

[pantry.pickles | Pickles & Olives | 2]
pickle = dill pickle, pickle spear; olive = kalamata, castelvetrano, green olive; giardiniera; caper; kimchi; sauerkraut;
pepperoncini; banana pepper; artichoke heart; sun dried tomato; roasted red pepper; pickled beet; pickled onion

[pantry.spices | Spices & Seasoning | 1]
spice; seasoning = seasoning mix, everything bagel seasoning, italian seasoning, cajun seasoning, lemon pepper;
taco seasoning = taco seasoning mix; salt = sea salt, kosher salt; black pepper = peppercorn, ground pepper;
cumin; paprika = smoked paprika; cinnamon; chili powder; garlic powder; onion powder; curry powder; turmeric;
red pepper flake = crushed red pepper; bay leaf; dried oregano; nutmeg; msg; furikake; za atar = zaatar

[pantry.baking | Baking | 1]
flour = all purpose flour, bread flour, almond flour; sugar = brown sugar, powdered sugar, cane sugar; baking soda;
baking powder; yeast; vanilla = vanilla extract; cocoa = cocoa powder; baking chip = semisweet chip, semi sweet chip, chocolate baking chip;
pancake mix = waffle mix, bisquick; cake mix = brownie mix, muffin mix; cornstarch; molasses; sprinkles

[pantry.oils | Oils & Vinegar | 2]
olive oil = extra virgin olive oil, evoo, graza; oil = vegetable oil, canola oil, cooking oil; avocado oil;
sesame oil = toasted sesame oil; coconut oil; vinegar = rice vinegar, apple cider vinegar, red wine vinegar,
white vinegar; balsamic = balsamic vinegar, balsamic glaze; cooking spray

[pantry.spreads | Nut Butters & Spreads | 2]
peanut butter = jif, jif natural, crunchy peanut butter, creamy peanut butter, natural peanut butter, pb2;
almond butter; nut butter; jam = jelly, preserve, fruit spread, smuckers jam; honey; maple syrup = syrup;
nutella = chocolate hazelnut spread

[pantry.breakfast | Cereal & Oats | 2]
cereal; granola; oatmeal = instant oatmeal, quaker instant oatmeal, quaker oatmeal, oatmeal packet;
oat = rolled oat, old fashioned oat, steel cut oat, quick oat, quaker oat; pop tart = toaster pastry

[pantry.meals | Shelf-Stable Meals | 3]
mac and cheese = macaroni and cheese, kraft dinner; instant noodle = cup noodle, instant ramen, maruchan, nissin;
boxed meal = hamburger helper, rice a roni, side dish; canned pasta = chef boyardee, mini ravioli, spaghettio, spaghetti o;
shelf meal = shelf stable meal, microwave meal, ready meal pouch, tasty bite, chana masala, tikka masala, dal, tadka,
yellow tadka, hayashi rice; ramen kit

[snacks.chips | Chips & Popcorn | 2]
chip = potato chip, tortilla chip, kettle chip, pita chip, veggie chip, sour cream onion, sour cream and onion; popcorn = utz popcorn, popcorn butter, microwave popcorn;
kettle corn = kettle popcorn, gg kettle corn; popped chip = popcorners, popcorner; pork rind; cheese puff = puff, cheeto

[snacks.crackers | Crackers & Pretzels | 2]
cracker = cheez it, triscuit, wheat thin, ritz, water cracker; pretzel = pretzel twist, rold gold, rold gold twist, snyders,
snyder; graham = graham cracker, gluten free graham, pepperidge farm graham; goldfish = pepperidge farm goldfish; rice cake

[snacks.nuts | Nuts & Trail Mix | 2]
nut = mixed nut; almond; cashew; peanut; pistachio; walnut; pecan; macadamia; trail mix; sunflower seed; pumpkin seed;
seed

[snacks.bars | Bars | 2]
protein bar = rxbar, quest bar, one bar; granola bar = nature valley, kind bar, chewy bar; energy bar = clif bar, clif

[snacks.sweets | Candy & Chocolate | 2]
candy = gummy, gummi, gummy bear, sour candy, joyride, hormbles; chocolate = dark chocolate, milk chocolate, chocolate bar,
peanut butter cup, chocolate bark, quinoa bark; licorice = red vine, twizzler; jelly bean; caramel; marshmallow; pudding; fruit snack

[snacks.cookies | Cookies | 2]
cookie = milano, pepperidge farm milano, oreo, biscotti, shortbread; wafer

[snacks.jerky | Jerky & Meat Sticks | 2]
jerky = beef jerky, turkey jerky; meat stick = chomps, turkey stick, beef stick, chomps turkey stick, slim jim

[drinks.coffee | Coffee | 2]
coffee = ground coffee, whole bean coffee, coffee bean, stumptown, stumptown holler, holler mountain, cafe bustelo coffee,
k cup, pod, instant coffee; espresso = cafe bustelo, cafe bustelo espresso, espresso concentrate, cold brew,
cold brew concentrate

[drinks.tea | Tea | 2]
tea = green tea, black tea, herbal tea, chamomile, iced tea, tea bag; chai = chai concentrate; matcha = matcha powder;
kombucha

[drinks.juice | Juice | 2]
juice = orange juice, apple juice, grape juice, cranberry juice, juice box; lemonade; juice pouch = capri sun,
capri sun pacific, capri sun wild cherry, capri sun pacific cooler

[drinks.soda | Soda & Sparkling | 2]
soda = cola, coke, sprite, ginger ale, root beer; sparkling water = seltzer, la croix, perrier, maison perrier, bubly,
sparkling spritz, topo chico, mineral water; prebiotic soda = olipop, poppi

[drinks.water | Water | 2]
water = spring water, bottled water, alkaline water; coconut water

[drinks.energy | Energy & Sports | 2]
energy drink = celsius, celsius energy, celsius sparkling, celsius fizz free, celsius watermelon, red bull, monster, ghost;
sports drink = gatorade, powerade, body armor, electrolit, electrolyte, electrolit grape, liquid iv, hydration

[drinks.protein | Protein & Shakes | 2]
protein shake = protein drink, core power, premier protein, fairlife shake; smoothie

[bar.whiskey | Whiskey | 2]
whiskey = whisky, bourbon, rye whiskey, rye, scotch, irish whiskey, japanese whisky, single malt, kaiyo, smokehead

[bar.gin | Gin | 2]
gin = london dry gin

[bar.vodka | Vodka | 2]
vodka

[bar.rum | Rum | 2]
rum = dark rum, white rum, spiced rum, aged rum

[bar.agave | Tequila & Mezcal | 2]
tequila; mezcal

[bar.spirits | Other Spirits | 2]
soju; baijiu; arak; ouzo; absinthe; pastis; aquavit; shochu; grappa; schnapps

[bar.brandy | Brandy & Cognac | 2]
brandy = cognac, armagnac, calvados, pisco

[bar.liqueur | Liqueurs | 2]
liqueur = amaretto, disaronno, kahlua, coffee liqueur, triple sec, cointreau, grand marnier, chartreuse, mr black, midori,
baileys, frangelico, st germain, limoncello, chambord, licor 43

[bar.amaro | Amari & Aperitifs | 2]
amaro = fernet, cynar, montenegro, averna, nonino, braulio; aperitif = campari, aperol, lillet, suze; vermouth = dry vermouth,
sweet vermouth, carpano, dolin

[bar.wine | Wine & Sake | 2]
wine = red wine, white wine, rose, cabernet, cabernet sauvignon, merlot, pinot noir, pinot grigio, chardonnay,
sauvignon blanc, riesling, malbec, zinfandel, black box, boxed wine, port, sherry, barolo, chianti, rioja, syrah, garnacha;
mead; umeshu = ume, plum liqueur; sparkling wine = champagne, prosecco, cava;
sake; plum wine = kikkoman plum

[bar.beer | Beer & Cider | 2]
beer = lager, ipa, ale, stout, pilsner, pacifico, corona, modelo, michelob, pacifico ballena, corona familiar;
cider = hard cider; hard seltzer = white claw, truly

[bar.bitters | Bitters | 2]
bitters = angostura, peychaud, orange bitters

[bar.mixers | Mixers & Syrups | 2]
mixer; tonic = tonic water; club soda; ginger beer; simple syrup = syrup mix; grenadine; orgeat; margarita mix;
bloody mary mix; cocktail cherry = maraschino, luxardo cherry; beer salt = twang, twang beer salt, rim salt;
garnish

[household.cleaning | Cleaning | 2]
detergent = laundry detergent, laundry pod; dish soap = dishwasher pod, dish detergent; cleaner = all purpose cleaner, wipe,
disinfecting wipe; bleach; sponge; trash bag = garbage bag

[household.paper | Paper & Wrap | 2]
paper towel; toilet paper; napkin; foil = aluminum foil; plastic wrap; parchment paper; zip bag = ziploc, storage bag;
tissue = kleenex

[household.personal | Personal Care | 2]
shampoo; conditioner; soap = hand soap, bar soap; body wash = shower gel; face towel = cleanskinclub, clean skin club,
clean skin club towel; toothpaste; mouthwash = therabreath; deodorant; skincare = lotion, moisturizer, la roche posay,
laroche posay, byoma, serum, cleanser, face wash; sunscreen; razor; toothbrush = electric toothbrush;
personal care device = braun, oral b, sonicare, shaver, trimmer; cotton = cotton swab, cotton round

[household.health | Health & Medicine | 2]
vitamin = multivitamin, olly, gummy vitamin, supplement, melatonin; medicine = allergy, up allergy, ibuprofen,
acetaminophen, pain relief, aspercreme, cold medicine; bandage = band aid; brace = futuro, futuro brace, wrap support,
knee brace, wrist brace

[household.pets | Pet | 2]
pet food = dog food, cat food, dog treat, cat treat; cat litter = litter

[household.other | Other Household | 2]
battery; light bulb; household

[other.other | Other | 0]
test

[modifiers | | 0]
chocolate chip; gluten free; organic; signature; signature select; lucerne; o organic; good and gather;
kirkland; sprouts brand; 365; fresh; natural; original; unsweetened; sweetened; low fat; reduced fat; family size;
value pack; usual; the usual; large; small; medium; jumbo; mini; style; select; classic; homestyle; blanco; reposado;
anejo; joven; extra; premium; fresh cut
"""

CATEGORIES: dict[str, dict] = {}   # "dept.cat" -> {"label", "dept", "tier"}
_PHRASES: dict[str, list[tuple[tuple[str, ...], str, str, int]]] = {}  # first token -> [(phrase, cat, generic, tier)]


def _parse():
    # A block runs from its [header] to the next one; entries may wrap across lines.
    blocks: list[tuple[str, str, int, list[str]]] = []
    for raw_line in _TABLE.strip().splitlines():
        line = raw_line.strip()
        if line.startswith("["):
            parts = [p.strip() for p in line.strip("[]").split("|")]
            blocks.append((parts[0], parts[1], int(parts[2]), []))
        elif line and blocks:
            blocks[-1][3].append(line)
    for cat, label, tier, lines in blocks:
        mod = cat == "modifiers"
        if not mod:
            CATEGORIES[cat] = {"label": label, "dept": cat.split(".")[0], "tier": tier}
        for entry in " ".join(lines).split(";"):
            entry = entry.strip().rstrip(",")
            if not entry:
                continue
            if "=" in entry:
                generic, alts = entry.split("=", 1)
                generic = generic.strip()
                syns = [generic] + [a.strip() for a in alts.split(",") if a.strip()]
            else:
                generic, syns = entry, [entry]
            for syn in syns:
                toks = tuple(tokens(syn))
                if toks:
                    _PHRASES.setdefault(toks[0], []).append(
                        (toks, None if mod else cat, generic, 0 if mod else tier))
    # every department can hold an uncategorised remainder
    for d, label in DEPARTMENTS:
        CATEGORIES.setdefault(f"{d}.other", {"label": f"Other {label.lower()}" if d != "other" else "Other",
                                             "dept": d, "tier": 0})
    for lst in _PHRASES.values():
        lst.sort(key=lambda p: -len(p[0]))


_parse()


def cat_label(cat: str | None) -> str:
    return CATEGORIES.get(cat or "", {}).get("label", "Unsorted")


def dept_of(cat: str | None) -> str:
    return (cat or "other.other").split(".")[0]


# The free-form `category` values the agent has written, mapped to a default home.
_LEGACY = {
    "produce": "produce.other", "vegetable": "produce.vegetables", "vegetables": "produce.vegetables",
    "fruit": "produce.fruit", "herb": "produce.herbs", "salad": "deli.salads",
    "meat": "meat.other", "protein": "meat.other", "seafood": "meat.seafood", "fish": "meat.seafood",
    "deli": "deli.meats", "deli meat": "deli.meats", "prepared": "deli.meals", "prepared food": "deli.meals",
    "ready meal": "deli.meals", "ready_meal": "deli.meals", "ready-meal": "deli.meals", "dip": "deli.dips",
    "dairy": "dairy.other", "dairy alternative": "dairy.plantmilk", "dairy-alt": "dairy.plantmilk",
    "cheese": "dairy.cheese", "eggs": "dairy.eggs", "bakery": "bakery.bread", "bread": "bakery.bread",
    "frozen": "frozen.other", "frozen-veg": "frozen.vegetables", "dessert": "snacks.sweets",
    "pantry": "pantry.other", "grain": "pantry.grains", "grains": "pantry.grains", "legumes": "pantry.beans",
    "canned": "pantry.canned", "broth": "pantry.broth", "sauce": "pantry.sauces", "condiment": "pantry.condiments",
    "ingredient": "pantry.other", "spice": "pantry.spices", "snack": "snacks.other", "treat": "snacks.sweets",
    "beverage": "drinks.other", "drink": "drinks.other", "alcohol": "bar.other",
}
_SUBCAT = {
    "spirit": "bar.other", "liqueur": "bar.liqueur", "wine": "bar.wine", "beer": "bar.beer", "bitters": "bar.bitters",
    "mixer": "bar.mixers", "syrup": "bar.mixers", "garnish": "bar.mixers", "aperitif": "bar.amaro", "whiskey": "bar.whiskey",
    "rice": "pantry.grains", "grain": "pantry.grains", "noodles": "pantry.pasta", "broth": "pantry.broth",
    "bone broth": "pantry.broth", "broth base": "pantry.broth", "vegetable broth": "pantry.broth", "sauce": "pantry.sauces",
    "curry mix": "pantry.sauces", "curry paste": "pantry.sauces", "meal sauce": "pantry.sauces", "pesto": "pantry.sauces",
    "tomato paste": "pantry.sauces", "salad kit": "produce.saladkits", "salad": "deli.salads", "cheese": "dairy.cheese",
    "yogurt": "dairy.yogurt", "eggs": "dairy.eggs", "creamer": "dairy.creamer", "milk alternative": "dairy.plantmilk",
    "dairy": "dairy.other", "meat": "meat.other", "deli meat": "deli.meats", "sausage": "meat.sausage", "tofu": "meat.plant",
    "burrito": "deli.meals", "frozen meal": "frozen.meals", "frozen entree": "frozen.meals", "frozen appetizer": "frozen.apps",
    "dumplings": "frozen.apps", "shelf-stable meal": "pantry.meals", "meal kit": "deli.meals", "soup": "pantry.canned",
    "canned tomatoes": "pantry.canned", "canned meat": "pantry.canned", "canned fruit": "pantry.canned",
    "canned fish": "pantry.canned", "olives": "pantry.pickles", "giardiniera": "pantry.pickles", "mustard": "pantry.condiments",
    "dip": "deli.dips", "juice": "drinks.juice", "herb": "produce.herbs", "produce": "produce.other",
    "microwave rice": "pantry.grains",
}
_SPIRIT = {
    "vodka": "bar.vodka", "gin": "bar.gin", "rum": "bar.rum", "tequila": "bar.agave", "mezcal": "bar.agave",
    "whiskey": "bar.whiskey", "bourbon": "bar.whiskey", "rye": "bar.whiskey", "scotch": "bar.whiskey", "irish": "bar.whiskey",
    "brandy": "bar.brandy", "cognac": "bar.brandy", "armagnac": "bar.brandy", "vermouth": "bar.amaro", "amaro": "bar.amaro",
    "aperitif": "bar.amaro", "liqueur": "bar.liqueur", "bitters": "bar.bitters", "wine": "bar.wine", "beer": "bar.beer",
}
# Receipt department headers -> a department to fall back on.
_SECTION = {
    "produce": "produce.other", "meat": "meat.other", "seafood": "meat.seafood", "deli": "deli.other",
    "bakery": "bakery.other", "liquor": "bar.other", "wine": "bar.wine", "beer": "bar.beer",
    "health and beauty": "household.personal", "health": "household.health", "beauty": "household.personal",
    "household": "household.other", "pharmacy": "household.health", "frozen": "frozen.other",
    "refrig/frozen": "dairy.other", "refrigerated": "dairy.other", "dairy": "dairy.other", "grocery": "pantry.other",
}
_READY = {"ready meal", "ready_meal", "ready-meal", "prepared", "prepared food"}
_RICE = {"rice", "jasmine rice", "basmati rice", "brown rice"}


def _matches(toks: list[str]):
    """All phrase hits, longest first, each consuming its tokens."""
    hits = []
    for i, t in enumerate(toks):
        for phrase, cat, generic, tier in _PHRASES.get(t, ()):
            n = len(phrase)
            if tuple(toks[i:i + n]) == phrase:
                hits.append((n, i, cat, generic, tier))
    hits.sort(key=lambda h: (-h[0], h[1]))
    used: set[int] = set()
    kept = []
    for n, i, cat, generic, tier in hits:
        span = set(range(i, i + n))
        if span & used:
            continue
        used |= span
        kept.append((n, i, cat, generic, tier))
    return kept


def _best(kept, prefer: str | None = None, lean: str | None = None):
    """Highest tier, then multi-word, then the later word. `prefer` (a dept or a dept.cat the
    agent was specific about) narrows the field when anything in it matched; `lean` (the loose
    legacy category's department) only breaks ties within a tier."""
    pool = kept
    if prefer:
        near = [k for k in kept if k[2] == prefer or dept_of(k[2]) == prefer]
        pool = near or kept
    return max(pool, key=lambda k: (k[4], bool(lean) and dept_of(k[2]) == lean, k[0] > 1, k[1]))


def match_generic(text: str | None, prefer: str | None = None, lean: str | None = None):
    """Best (cat, generic, tier) for a piece of text, or None."""
    kept = [k for k in _matches(tokens(text)) if k[2]]
    if not kept:
        return None
    n, i, cat, generic, tier = _best(kept, prefer, lean)
    return cat, generic, tier


# Agent-written hints worth more than the name when they are specific: they settle a tie the
# other way ("Lundberg microwave rice chili crisp" is rice; "Root Beer Schnapps" is a liqueur).
_WEAK_SUBCAT = {"dairy", "meat", "produce", "rice", "spirit", "grain"}
_BAR_HINTS = {"spirit", "liqueur", "wine", "beer", "aperitif", "bitters", "whiskey"}
_LEGACY_DEPT = {
    "produce": "produce", "vegetable": "produce", "vegetables": "produce", "fruit": "produce", "herb": "produce",
    "meat": "meat", "protein": "meat", "seafood": "meat", "fish": "meat", "dairy": "dairy", "cheese": "dairy",
    "eggs": "dairy", "bakery": "bakery", "bread": "bakery", "frozen": "frozen", "deli": "deli", "deli meat": "deli",
    "dip": "deli", "grain": "pantry", "grains": "pantry", "legumes": "pantry", "canned": "pantry", "broth": "pantry",
    "sauce": "pantry", "condiment": "pantry", "spice": "pantry",
}
_MEALS = ("deli.meals", "pantry.meals", "frozen.meals")


def _family(cat: str | None) -> str:
    """Meals are one family across deli, pantry and frozen: storage decides which."""
    return "meals" if cat in _MEALS else dept_of(cat)


def classify(name: str | None = None, brand: str | None = None, category: str | None = None,
             subCategory: str | None = None, spiritType: str | None = None, storage: str | None = None,
             section: str | None = None, raw: str | None = None) -> dict:
    """Place a thing in the taxonomy. Returns {dept, cat, generic, confidence, via}."""
    legacy = (category or "").strip().lower()
    sub = (subCategory or "").strip().lower()
    spirit = (spiritType or "").strip().lower()
    quals = parens(name)
    prepared = any(q.startswith(("prepared", "premade", "pre-made")) for q in quals) or legacy in _READY

    # What the agent said outranks a name match only when it is specific.
    hint = None
    if spirit in _SPIRIT or sub in _BAR_HINTS:
        hint = _SPIRIT.get(spirit) or _SUBCAT.get(sub)
    elif sub in _SUBCAT and sub not in _WEAK_SUBCAT:
        hint = _SUBCAT[sub]
    prefer = dept_of(hint) if hint and dept_of(hint) == "bar" else hint
    lean = _LEGACY_DEPT.get(legacy)

    hit, via = None, None
    texts = ((name, "name"), (f"{brand or ''} {name or ''}", "brand"),
             (" ".join(quals), "name"), (expand_receipt(raw) if raw else None, "receipt"))
    for text, v in texts:
        if text and text.strip():
            hit = match_generic(text, prefer, lean)
            if hit:
                via = v
                break

    tier = 0
    if hint and (not hit or _family(hit[0]) != _family(hint)):
        # nothing matched, or the name points somewhere the hint rules out: the hint wins
        cat, generic, tier = hint, None, 2
        via, conf = ("spiritType" if spirit in _SPIRIT else "subCategory"), "medium"
    elif hit:
        cat, generic, tier = hit
        conf = "high" if tier >= 2 or via != "receipt" else "medium"
        if hint and hint != cat and not hint.endswith(".other") and _family(hint) != "meals":
            cat, generic = hint, None  # same department, the agent was more specific
        if generic in _RICE and any(t.startswith("microwav")
                                    for t in tokens(f"{name or ''} {raw or ''}", keep_parens=True)):
            generic = "microwave rice"
    else:
        generic, conf = None, "low"
        if legacy in _LEGACY:
            cat, via = _LEGACY[legacy], "category"
        elif section and section.strip().lower() in _SECTION:
            cat, via = _SECTION[section.strip().lower()], "section"
        else:
            cat, via = "other.other", "fallback"

    # deli-counter meat: the agent filed it under deli
    if legacy in ("deli", "deli meat") and dept_of(cat) == "meat" and cat != "meat.plant":
        cat = "deli.meats"

    st = (storage or "").strip().lower()
    if prepared and tier < 3 and dept_of(cat) != "deli" and not hint:
        cat = "deli.meals"
    if prepared and cat == "pantry.canned" and st == "fridge":
        cat = "deli.meals"  # a fresh soup from the deli case, not a can
    if cat in _MEALS:
        if st == "freezer":
            cat = "frozen.meals"
        elif st in ("cupboard", "pantry"):
            cat = "pantry.meals"
        elif cat == "frozen.meals":
            cat = "deli.meals"
    elif st == "freezer":
        if cat in ("produce.vegetables", "produce.greens"):
            cat = "frozen.vegetables"
        elif cat == "produce.fruit":
            cat = "frozen.fruit"
    return {"dept": dept_of(cat), "cat": cat, "generic": generic, "confidence": conf, "via": via}
