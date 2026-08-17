# Arctic Vibe — Mod Changelog

Ships as **Arctic Vibe** (`skin.arctic.vibe`, v1.0.3) by sea6ull.
A modified fork of **Arctic Fuse 2** (`skin.arctic.fuse.2`, v2.12.12) by jurialmunkey.

This document records every change made to the upstream skin, and — where it matters —
*why* a particular implementation was chosen. It is written to be handed to a future
assistant with no prior context, so it includes the traps and coupled behaviours that
are not obvious from reading the XML.

All modified lines are tagged with an `SKINMOD:` comment in the source, so
`grep -rn "SKINMOD" .` will list every touched location.

---

## Conventions used throughout

* The skin's coordinate space is **1920x1080** (`addon.xml` declares 1080i for all aspect ratios).
  1% of screen height = 10.8px.
* The skin uses a `-name` convention for negative constants (e.g. `hub_widgets_shift_y` /
  `-hub_widgets_shift_y`). **Both must be kept in sync.**
* Kodi constants **cannot do arithmetic**. Any "base + offset" value is pre-computed and
  stored as its own constant, with the arithmetic recorded in the comment.
* Files under `shortcuts/generator/` are **templates**. Editing them requires a
  menu/shortcut rebuild in Kodi before the change takes effect, because the real includes
  are generated at runtime by `script.skinvariables`.

---

## 1. Spotlight removed entirely

Spotlight was referenced in 313 places. Removal was done in two passes:

1. **Constant-folding.** Every `Skin.HasSetting(Hub.<X>.DisableSpotlight)` was replaced with
   `True` and every negated form with `False` (281 replacements). This makes the skin behave
   as though Spotlight were disabled on every hub, which is exactly the "no Spotlight" layout.
2. **Dead-code deletion.** The blocks that constant-folding made unreachable were removed.

Deleted include definitions (`1080i/Includes_Hubs.xml`):
`Hub_Spotlight`, `Hub_SpotlightFanart`, `Hub_Slide_Spotlight_OnFanart`,
`Hub_Slide_Spotlight_OnWidgets`, `Hub_Categories_Spotlight_Viewline`.

Other changes:
* `1080i/Includes_Spotlight.xml` replaced with **empty stubs** (`Spotlight_List`, `Spotlight_Main`,
  `Spotlight_Info`, `Spotlight_Fallback_Slideshow`, `Spotlight_Main_Button_Standard`). The names are
  kept so any residual reference still resolves at skin load instead of erroring.
* Spotlight options grouplist (176 lines) removed from `1080i/Includes_Shortcuts_Options.xml`.
* Spotlight label + options entry removed from `1080i/Custom_1115_Dialog_Shortcuts_Window.xml`.
* Spotlight trailer-autoplay setting removed from `1080i/Includes_SkinSettings.xml`.
* Spotlight menu-button setting removed from `1080i/Includes_Items.xml`, and its
  `Label_Setting_Spotlight_UseMenuButton` variable removed from `1080i/Includes_Labels.xml`.
* Generator stage `shortcuts/generator/data/base/category_spotlight.xml` unhooked from
  `shortcuts/generator/data/base/hubs_complex.xml`.
* Constants `hub_spotlight_*` and `hub_group_fanartspotlight_s` deleted.

### Trap that was hit and fixed
Removing the generator stage orphaned `<include>skinvariables-$PARAM[categories]-spotlight</include>`
in `Includes_Hubs.xml`. Left in place it is a **dangling include reference and a skin-load error**.
It was removed.

### Side effect worth knowing
`Hub_Slide_Group_OnWidgets` was gated on the Spotlight condition. With that condition now
constant-false, the menu group sits at a **permanent -440px offset** (`hub_group_widgetup_s`).
This is not a regression — it is exactly what Spotlight-disabled hubs did before — but it means
every hub now behaves like the old "no Spotlight" page.

### Left deliberately in place
`spotlight_label` / `spotlight_path` keys remain in the shortcut JSON defaults
(`shortcuts/skinvariables-shortcut-*.json`, `shortcuts/prebuilt/**`). They are inert data with no
consumer; editing JSON structure risks breaking shortcut config for no visual gain.

---

## 2. Footer settings section and bottom furniture removed

* The "Footer" section (label + Clock / Studio / Now Playing toggles) removed from
  `1080i/Includes_SkinSettings.xml`.
* In `1080i/Includes_Furniture.xml`, the definitions of `Furniture_Bottom_Right`,
  `Furniture_Bottom_Left`, `_Furniture_Bottom_Left` and `Furniture_Bottom` were **emptied**
  rather than deleting their ~30 call sites. Every existing
  `<include content="Furniture_Bottom_Right">` still resolves; it just renders nothing.
  Single point of change, trivially reversible.

### Left deliberately in place
`View_Furniture_Bottom_ViewLine` is **untouched**. Despite the name it is a generic label helper
also used for scrollbar page counts, sort indicators, weather and search headers. Emptying it
would break functional UI.

---

## 3. Widget rows moved down (more fanart space)

**Key file:** `1080i/Includes_Constants.xml`

```xml
<constant name="hub_widgets_shift_y">140</constant>
<constant name="-hub_widgets_shift_y">-140</constant>
<constant name="hub_widgets_shift_y_detailed">580</constant>   <!-- 440 + 140 -->
<constant name="-hub_widgets_shift_y_detailed">-580</constant>
```

Applied as `<top>` / `<bottom>` on the **wrapper group id 340** in both `Hub_Standard` and
`Hub_Widgets_Only` (`1080i/Includes_Hubs.xml`). Result: row 1 moved from ~55% to ~68% down the
screen, so the row below (title included) falls off the bottom edge.

### Why the wrapper and not the grouplist
`widget_grouplist_t` (880) and `widget_tbump_h` (640) feed a **scrolling** grouplist whose resting
position depends on runtime focus and a 640px top bumper. That math could not be derived reliably
from the XML. A `<top>` on the wrapper group translates the entire widget block by exactly N pixels
**regardless of the internal scroll math**, and provably cannot alter row-to-row spacing.

### FAILED APPROACH — do not repeat
An intermediate attempt also changed `widget_items_h` (1010 → 1064) to increase row *pitch*.
**This broke the widget titles**: they disappeared when scrolling to row 2. Reason —
`widget_items_h` is the height of each widget's row group and therefore feeds the grouplist's
item-to-item advance, but the row's *title* is positioned by a separate constant
(`widget_label_t`, -580) as an offset from its own layout slot. Growing the row height moved each
slot without moving the label offset with it, pushing titles outside the visible band.

**Pitch in this skin is a relationship between at least three constants, not one.**
`widget_items_h` is back at its stock 1010 and should not be used as a pitch lever.

---

## 4. Fanart (FlixArt background) enlarged

The FlixArt panel is anchored top-right and sized by `flixart_size_w` / `flixart_size_h`.
Height increased by **140px** (matching the widget row shift) so the original
fanart-to-widget relationship is preserved, just 140px lower.

Width was increased proportionally because `Background_FlixArt` uses `aspectratio scale`, which
preserves the source aspect and **crops** the overflow. Every original panel was exactly 16:9, so
adding height alone would have zoomed in and cropped the sides of every image. All variants are
held at 16:9 (1.7779 vs 16:9 = 1.7778).

`Background_Blur_Quadrants` reads the same two constants, so the blurred surround re-flows
automatically — no separate edit needed.

See section 9 for the final values after the Small/Medium/Large rework.

---

## 5. Setup wizard: widget mode step removed, Advanced forced

The wizard (`1080i/Custom_1180_Wizard.xml` + `Items_Wizard` in `1080i/Includes_Items.xml`) had three
steps: Themes → Dialog → Layout. The Layout step offered Basic / Detailed / Advanced.

**Advanced is now the only mode and the default.** Advanced is simply the "both settings unset"
state, so it is applied with `Skin.Reset(Hub.Home.DisableSubmenu)` +
`Skin.Reset(Hub.Home.EnableDetailedInformation)`.

### Critical trap — the Layout step was doing hidden work
Each of the three mode items also ran `Skin.Reset(Hub.Home.ReplaceWindow)`, triggered the shortcut
rebuild via `Skin.SetString(Shortcuts.RebuildDateTime,...)`, closed the wizard with `PreviousMenu`,
and **launched the menu-preset chooser** (`library-basic` vs `tmdb-basic`) that populates the home
menu. Deleting the step outright leaves the wizard stranded on the Dialog step with no way to
finish and **no home menu ever built.**

That work was redistributed:
* The **Dialog step is now the final step.** It applies Advanced, triggers the rebuild, then reloads.
* Because `ReloadSkin()` destroys the window mid-sequence, the close-out and preset chooser could
  not stay in the same `onclick` chain. They now live on a **new hidden button, id 8002**, in
  `Custom_1180_Wizard.xml`, reached via the skin's existing `AlarmClock(reset,SetFocus(8002),...)`
  pattern after the reload completes.
  **Superseded by §14** — 8002 still exists and still runs at the same point, but it no longer
  prompts; there is only one preset left and it is applied silently.
* Next / Back buttons and the back-nav hidden button (8001) were updated so none of them still
  point at the removed step.

Note: id 8002 is unique *within window 1180*. Other `8002` occurrences in `Includes_Items.xml` are
`Settings_Button` params in unrelated dialogs and do not conflict.

Also removed: the Mode selector (id 390) from `Custom_1115_Dialog_Shortcuts_Window.xml`; the widget
mode preview items in `Includes_Items.xml` (stubbed as `Items_Preview_WidgetsMode`, since
`Custom_1112_Dialog_PreviewSettings.xml` and `Items_Preview_CustomDialog` reference them);
`Shortcuts_Window_Var_Label_ClassicMode`, `Shortcuts_Window_Var_Label_ClassicMode_Preselect` and
`Shortcuts_Window_Var_Image_ClassicMode` from `Includes_Shortcuts_Window.xml`;
`Exp_Var_HomeLayout_IsBasic/IsDetailed/IsAdvanced` from `Includes_Expressions.xml`; and the
`extras/screenshots/layouts/` folder (Basic.jpg, Detailed.jpg, Advanced.jpg, ~1.7MB).

### Not migrated
Hubs previously set to Basic or Detailed by hand **keep their stored setting** — nothing migrates
them. A one-time reset across all hubs was offered but not implemented.

---

## 6. "Blur background — Basic (widgets)" setting removed

Removed from `Items_Settings_WidgetStyle` in `1080i/Includes_Items.xml`, along with
`Label_Settings_WidgetBlurBackground` in `1080i/Includes_Labels.xml`.

### This setting was NOT dead — important
The label read "Basic (widgets)" (`$LOCALIZE[10036] ($LOCALIZE[31103])`), naming the removed Basic
mode. But the underlying setting `Widgets.DontHideArtworkOnClassic` feeds `widget_hideartwork`,
which fires `SetProperty(Background.HideArtwork,True)` on widget focus — it **hides the fanart
outright**. On hub pages it is inert (it requires `widgets_only`, false in Advanced), but
`shortcuts/generator/data/base/home_widgets.xml` hardcodes `grouplist_widgets_only` to `True`,
so it was **still live on the Home window**.

Removing only the UI would have frozen it at its stored value — on default, hiding the (newly
enlarged) fanart with no way to switch it off. So:

* `shortcuts/generator/data/parts/widgets_row.xmltemplate` — `widget_hideartwork` forced to `False`.
  **Generator template: needs a menu/shortcut rebuild to take effect.**

### Still read the setting (deliberately left alone)
* `shortcuts/generator/data/parts/search_row.xmltemplate` — search result rows.
* `1080i/Includes_DialogInfo.xml` — widget rows inside the info dialog (~28 sites).
* `1080i/Includes_Lists.xml` — gated on the `hide_artwork` property, which is now never set for
  widget rows, so this path is inert for them.

Both remaining consumers freeze at the user's stored value. Forcing them to never-hide was offered
but not implemented.

---

## 7. Options menu opens on hover

`1080i/Includes_Hubs.xml`, `Hub_Categories_Options`, **button id 308**:

```xml
<onfocus condition="!Window.IsActive(1170)">ActivateWindow(1170)</onfocus>
```

The original `onclick` chain (`SetProperty(307Refocus,308)` + `SetFocus(307)`) was **kept** so mouse
clicks and the existing hidden-button-307 route still work.

### Known risks (verified working in practice, but worth knowing)
* **Reopen loop.** Closing dialog 1170 returns focus to 308. If Kodi re-fires `onfocus` on that
  return, the menu would reopen and trap the user. The `!Window.IsActive(1170)` guard does not
  cover this case (the dialog is closed by then). Fallback if it ever regresses: a latch property
  cleared via `<onunfocus>`.
* **Search reachability.** In the nav chain 308 sits between the category list and Search (309):
  `onleft` → categories (300), `onright` → 309. Search is therefore only reachable by passing
  *through* Options.

---

## 8. Row views moved down; Wall view rebuilt; default widget view changed

### 8a. Row views (`1080i/Includes_Views_Row.xml`)

```xml
<constant name="view_row">510</constant>                      <!-- unchanged, used by Combined -->
<constant name="view_row_shifted">650</constant>              <!-- 510 + 140 -->
<constant name="view_row_hitrect_y">586</constant>
<constant name="view_row_hitrect_y_shifted">726</constant>    <!-- 586 + 140 -->
```

All eight row-based views are shifted down 140px, matching the hub widget rows:
Square (50), Landscape (51), Poster (52), Circle (53), Card (54), Board (57), Quad (58), Disc (59).

Implemented by shifting the **defaults** of the shared `View_Row_Include` wrapper:

```xml
<param name="rowtop">view_row_shifted</param>
<param name="hitrect_y">view_row_hitrect_y_shifted</param>
```

The mouse hit rect was moved by the same 140 so targeting follows the artwork.

**Why not just change `view_row`:** that constant is also read by the **Combined** views
(502–592) in `1080i/Includes_Views_Combined.xml`, which must stay at their stock position.
Because the shift lives on the row wrapper's defaults and Combined positions its views with the
plain `view_row` constant directly, the two groups are structurally separate and Combined cannot
be caught up by accident.

Per-view override is still available — pass `rowtop` / `hitrect_y` to one view (useful if Quad,
the tallest of the eight, ever needs a smaller offset).

Card (54) and Board (57) each have two `View_Row_Include` calls behind their
`View.UseTextBasedCard` / `View.UseTextBasedBoard` toggles; both inherit the shift.

### 8b. Wall view — true grid (`1080i/Includes_Views_Wall.xml`)

**The AF2 skin already contained this layout.** `View_Wall_Include` had two branches; the one
gated behind `Exp_View_WallVert` sets the panel `orientation` to `vertical`, which fills
**row-major, 7 across**, wrapping to the next row. That branch is functionally identical to
Arctic Fuse 3's Wall view. AF3's only real change was deleting the other branch.

The default branch inherited `orientation: horizontal`, which fills **column-major** (item 1,
item 2 directly below it, item 3 in the next column) and scrolls sideways.

Geometry confirms 7 across was always intended: `view_poster_itemlayout_w` is `257.14` in **both**
AF2 and AF3 — exactly 1800 ÷ 7 — and the panel spans the full 1920.

Changes made:
* `1080i/Includes_Expressions.xml` — `Exp_View_WallVert` forced to `True`.
  The expression was **kept rather than deleted** so `Exp_View_Scrollbar_Maxi_V`
  (which references it) still resolves and the vertical scrollbar shows.
  This also lifts the original 16:9-only restriction, so the grid works on all aspect ratios.
* The column-major branch was deleted, and the redundant condition dropped from the survivor.
* The "Switch Wall Orientation" setting (`View.SwitchWallOrientation`) removed from
  `Items_Settings_Viewmodes` in `Includes_Items.xml`, and `Label_Settings_WallOrientation`
  removed from `Includes_Labels.xml`.

#### AF3 dependencies deliberately NOT copied
AF3's Wall block contains `<onleft>ActivateWindow(1171)</onleft>` and
`SetProperty(InfoPanel.FullSwitch,Wall,Home)`. Window **1171** (`Custom_1171_Dialog_Views.xml`)
does not exist in AF2, and `InfoPanel.FullSwitch` has zero references in AF2. Lifting AF3's block
wholesale would produce a dead left-navigation. AF2's `<onleft>600</onleft>` was kept.

#### Geometry difference left on the table
AF3 positions the grid at `top 290` / `bottom -40` (830px tall, deliberately running 40px past the
bottom edge so the last row is clipped as a scroll hint). AF2's values `top 200` / `bottom 140`
(740px) were **kept**, because they are tuned to AF2's header and 290 risks a gap or collision.

### 8c. Default widget viewtype: Landscape → Poster

`shortcuts/generator/data/setup/widgets_row.xml` — the final `<condition>True</condition>` fallback
in the `widget_include` rules changed from `List_Landscape_Row` to `List_Poster_Row`.
**Generator template: needs a menu/shortcut rebuild.**

This is the true default, used when a widget's style shows as "Default" in the widget options.

#### Scope limitation — expect this to look like it did nothing
Most widgets in the shipped presets do **not** rely on the default; they name Landscape
explicitly — 6 times in `shortcuts/skinvariables-shortcut-homemenu.json`, 10 in
`shortcuts/skinvariables-shortcut-sidemenu.json`, plus copies under `shortcuts/prebuilt/`.
Those keep their stored style, as does any already-configured widget. After a rebuild, Poster
appears only on widgets set to "Default". Rewriting those 30-odd explicit declarations was offered
but not implemented — they are deliberate per-widget choices (in-progress episodes, for instance,
may want landscape).

---

## 9. Background Style renamed to Small / Medium / Large, Medium default

Setting key is unchanged: `Skin.String(Skin.FlixArt.Size)`.

| Option | Stored value | Panel size | Old name |
|---|---|---|---|
| Small | `Small` | 1529x860 | Standard (was default) |
| **Medium** | *unset* (or `Medium`) | **1689x950** | Large |
| Large | `Large` | 1849x1040 | Extra Large |

All are 16:9 (see section 4).

**Medium is the default**, implemented by putting its values in the **base**
`1080i/Includes_Constants.xml`, so an unset setting falls through to Medium. Only Small and Large
need override files:

* **New file** `1080i/Includes_Constants_FlixArt_Small.xml` (1529x860).
* `1080i/Includes_Constants_FlixArt_Large.xml` now holds 1849x1040.
* `1080i/Includes_Constants_FlixArt_ExtraLarge.xml` **deleted**.
* `1080i/Includes.xml` conditions updated accordingly.

In `1080i/Includes_Items.xml` the Medium radio uses
`[String.IsEmpty(Skin.String(Skin.FlixArt.Size)) | Skin.String(Skin.FlixArt.Size,Medium)]` and its
`onclick` is `Skin.Reset(...)` rather than `SetString`, so the default state stays clean.
The `label2` dimension captions were also corrected — they still showed the pre-mod
1280x720 / 1440x810 / 1600x900 values.

New strings `#31598` ("Small") and `#31599` ("Medium") appended to all 13 language files.
`#31439` ("Large") is reused; `#31440` ("Extra Large") is now unused but left in place.

### Aspect-ratio interaction
`Includes_Constants_4x3.xml` and `Includes_Constants_3x2.xml` also define `flixart_size_*`
(1209x680) and are included **after** the Small/Large files but **before** the base constants.
Since the first definition wins in Kodi, 4:3 and 3:2 screens keep their own default, but an
explicit Small or Large selection overrides them and may overflow a narrower screen.
This ordering is inherited from upstream and was not changed.

---

## 10. Renamed to Arctic Vibe

* `addon.xml` — `name` changed to `Arctic Vibe`; `description` updated and now credits the
  upstream skin. **The first loading screen text comes from this `name` attribute** (Kodi's own
  skin-load splash), not from any skin XML — `Custom_1198_Window_Startup.xml` only shows the Kodi
  logo plus "Initialising Skin".
* All 13 `language/resource.language.*/strings.po` files — 38 occurrences of
  "Arctic Fuse 2" / "Arctic Fuse2" / "Arctic Fuse" replaced with "Arctic Vibe" (longest pattern
  first, so no stray `2` is left behind). `msgctxt` IDs are untouched.

### Addon id renamed to `skin.arctic.vibe`
The id was initially left as `skin.arctic.fuse.2` to preserve userdata; it has now been renamed.

* `addon.xml` — `id="skin.arctic.vibe"`.
* **Containing folder renamed to `skin.arctic.vibe`.** Kodi requires the addon directory name to
  match the id exactly; a mismatch makes Kodi register the addon under the folder name and can
  produce duplicate/ghost entries.
* 22 internal references updated across 11 files. These are **functional**, not cosmetic:
  * `shortcuts/skinvariables-generator.json` — `"skinid"`. `script.skinvariables` uses this to
    locate the skin and write generated includes. A stale value breaks menu/widget generation.
  * `1080i/Includes_Paths.xml` — `Path_SkinVariables_SkinFolder` (x2), used to build
    `addon_data` paths for skin users and prebuilt menu copying.
  * `1080i/Custom_1195_SkinUserLoginScreen.xml` — `skinid=` in the plugin:// content path.
  * `1080i/Includes_Labels.xml` (x2), `1080i/Includes_Settings.xml`, `1080i/DialogAddonInfo.xml` —
    `System.AddonVersion(<id>)` in the version line. A stale id renders an empty version.
  * `1080i/Settings.xml` (x8) — `<label2>` addon-id captions in the settings UI.
  * `.github/*` (CI metadata, not shipped), `language/resource.language.de_de/strings.po` (a header
    comment).

#### Userdata consequence — settings will NOT carry over
Skin settings, configured home/side menus and widget assignments live in
`userdata/addon_data/<addon id>/`. Because the id changed, Kodi now reads
`userdata/addon_data/skin.arctic.vibe/`, so the skin starts **unconfigured** and the setup wizard
runs again.

To preserve an existing configuration, copy the old directory across **before** first launch:

```
userdata/addon_data/skin.arctic.fuse.2/  ->  userdata/addon_data/skin.arctic.vibe/
```

Note `settings.xml` inside it may contain the old id in stored path strings (e.g. a customised
`Startup.ImageFolder` pointing at `special://skin/...` is fine, but absolute
`.../addons/skin.arctic.fuse.2/...` paths are not). Grep the copied file for `arctic.fuse` and fix
any absolute paths.

### Version set to 1.0
`addon.xml` `version` changed from `2.12.12` to **`1.0`** (this fork's own versioning).

**This is also what makes the rename actually appear.** Kodi caches addon metadata — including the
display name — in its addon database. Reinstalling a zip whose `version` is unchanged makes Kodi
skip the metadata refresh, so the old name persists on the skin-load splash even though `addon.xml`
is correct. Changing the version forces the re-read.

If the name is ever stale again after editing `addon.xml`, bump the version rather than hunting for
the string in the skin XML — it is not there.

### provider-name
Changed from `jurialmunkey` to **`sea6ull`**. Upstream credit is retained in the `<description>`.

### The startup wordmark — `Object_StartUp_Logo` (THE actual loading screen)
**This is the screen that shows "Arctic Fuse²" a moment after the Kodi splash.** It is
`Object_StartUp_Logo` in `1080i/Includes_Objects.xml`, drawn by `Hub_Widget_Splash` from
`Custom_1198_Window_Startup.xml`.

It was **not** findable with `grep "Arctic Fuse"`, because the name is split across inline markup
and two separate controls:

```xml
<label>[COLOR=main_logo]Arctic[/COLOR] Fuse</label>   <!-- font_title_huge, ColorHighlight -->
<label>$NUMBER[2]</label>                            <!-- separate control, font_head_bold -->
```

The literal text is `[COLOR=main_logo]Arctic[/COLOR] Fuse`, so the substring "Arctic Fuse" does not
exist in the file. The "2" is its own label in a horizontal grouplist with a smaller font, which is
why it renders raised like an exponent. `[COLOR=main_logo]` forces "Arctic" to the soft white text
colour while the rest of the label inherits `<textcolor>$VAR[ColorHighlight]</textcolor>`, which is
why only the second word tracked the user's chosen highlight colour.

**Fix:** the label is now `[COLOR=main_logo]Arctic[/COLOR] Vibe` and the `$NUMBER[2]` control has
been deleted.

**Lesson for future searches:** when hunting for on-screen text in this skin, grep for single words
(`Fuse`, `Arctic`) and remember that `[COLOR=...]`, `[B]`, `[UPPERCASE]` tags and multi-control
grouplists routinely split visible strings. Also note the `2` was `$NUMBER[2]`, not a literal `2`.

### icon.png rebranded (separate from the above)
`icon.png` (720x720) also had "arctic fuse 2" baked in as pixels — Kodi shows it as the addon icon
in the addon browser and skin settings. This was **not** the loading screen (an earlier diagnosis
wrongly claimed it was), but it did need rebranding anyway.

`icon.png` was rebranded in place:
* The word "arctic" (dark slate `#2D3E50`-ish, x 74-360, y 148-237) was **kept as-is**.
* "fuse" (light `#C9DEEF` = RGB 201,222,239, x 143-361, y 242-333) and the "2" (bottom right,
  x 649-688, y 612-675) were removed by diffusion inpainting — the rectangles were seeded with a
  vertical interpolation between the rows just outside them, iteratively Gaussian-blurred, then
  composited back through a feathered mask so no rectangular seam remains.
  *Note:* the "fuse" box top edge must sit at y>=240, because "arctic" descends to y=237 — seeding
  from y=229 drags dark glyph pixels into the fill and produces a visible dark patch.
* "vibe" was rendered in `fonts/Figtree-Bold.ttf` at size **122**, colour RGB(201,222,239), and
  positioned by measured glyph bbox so it lands exactly on the original's box
  (right edge x=361, top y=242, height 91px). The two words are right-aligned in the original
  layout, hence aligning to the right edge rather than the left.
* Saved back as RGBA to match the original mode.

`fanart.jpg` was checked and contains **no text** — no change needed.

If the icon ever needs redoing, the original is recoverable from the upstream repo; the plate and
the metrics above are enough to reproduce the edit.

---

## 11. Version / build line in Settings

Four places rendered `$INFO[System.BuildVersion,Kodi ,]$INFO[System.AddonVersion(<id>), • AF2 v,]`:
`1080i/Includes_Labels.xml` (x2), `1080i/Includes_Settings.xml`, `1080i/DialogAddonInfo.xml`.

All four now read:

```
$INFO[System.BuildVersionShort,Kodi ,]$INFO[System.AddonVersion(skin.arctic.vibe), • AV v,]
```

* `System.BuildVersion` → **`System.BuildVersionShort`** drops the `Git:<date>-<hash>` suffix.
* `AF2 v` → `AV v`.
* The version number itself is **not hardcoded** — it is read live from `addon.xml` via
  `System.AddonVersion`, which is why the `version` attribute there is what this line renders —
  currently `version="1.0.3"`, so it reads "AV v1.0.3". Keep it that way; hardcoding would let the
  two drift apart.

The info label targets the addon id, which is now `skin.arctic.vibe` (see section 10).

---

## 12. New setting: hide the profile picture on the idle "Now Playing" tile

**Setting key:** `Skin.HasSetting(ButtonMenu.ShowProfilePicture)` — **default off (unset)**.
**UI location:** Skin Settings → **Layout** → **Other**, last entry, labelled
`$LOCALIZE[33063] > $LOCALIZE[31603]` → "Options > Show profile picture".

### The behaviour being changed
The Options dialog (`Custom_1170_Dialog_HomeMenu.xml`) draws up to four configurable tiles. A tile
whose `OptionsTiles.NN.Include` string is `NowPlaying` renders `ButtonMenu_NowPlayingClock`
(`1080i/Includes_ButtonMenu.xml`). Tile 01 **defaults to `NowPlaying`**
(`Includes_Actions.xml`, line ~61), so this is the shipped configuration, not an edge case.

With nothing playing, that tile's two labels fall through to the date
(`Label_ButtonMenu_NowPlayingTitle` → `System.Date(DDD)`,
`Label_ButtonMenu_NowPlayingSubLabel` → `System.Date(d MMM YYYY)`) and its thumb falls through to
`Image_ProfileThumb` via the final unconditional `<value>` of
`Image_ButtonMenu_NowPlayingThumb` (`1080i/Includes_Images.xml`). The new setting suppresses that
thumb; the date labels are untouched.

### Why the icon is hidden by `<visible>`, not by emptying the texture
The obvious fix — making the profile-thumb fallback in `Image_ButtonMenu_NowPlayingThumb`
conditional so the variable resolves empty — **does not work**. `ButtonMenu_NowPlayingClock` passes
`icon_buttonstyle=true`, and in that branch `_Dialog_Special_Button`
(`1080i/Includes_Dialogs.xml`) draws the artwork *plus* a `circle_50.png` backdrop disc coloured
with `$PARAM[textcolor]`. Blanking the texture leaves a solid coloured circle floating on the tile.
The whole 80x80 icon group has to go.

So a new param was added:

* `_Dialog_Special_Button` — `<param name="icon_visible">true</param>` default, applied as
  `<visible>$PARAM[icon_visible]</visible>` on the 80x80 icon group (covers both the
  `icon_buttonstyle` and plain-image branches).
* `Dialog_Special_Button` — same `true` default, forwarded to **both** of its
  `_Dialog_Special_Button` calls (the unfocused and focused copies). Missing either one makes the
  picture reappear on focus.

`Dialog_Special_Button` has ~30 call sites across the dialogs; every one of them omits
`icon_visible` and therefore keeps the `true` default. Notably `ButtonMenu_Profile` — the *other*
tile that shows `Image_ProfileThumb` — is deliberately unaffected: that tile's whole purpose is the
profile, and it is not what the setting names.

### The visibility condition
`1080i/Includes_Expressions.xml`:

```xml
<expression name="Exp_ButtonMenu_ShowNowPlayingThumb">[Skin.HasSetting(ButtonMenu.ShowProfilePicture) | !String.IsEmpty(Pvr.EPGEventIcon) | !String.IsEmpty(Player.Art(poster)) | !String.IsEmpty(Player.Art(tvshow.poster)) | !String.IsEmpty(Player.Art(thumb))]</expression>
```

The four art checks are **exactly the conditions of the four real `<value>` entries in
`Image_ButtonMenu_NowPlayingThumb`, in the same order**. That is the point: the thumb is hidden
precisely when the variable would have fallen through to the profile picture, and never when there
is genuine artwork to show. `Player.HasMedia` was deliberately *not* used — something can be
playing with no artwork at all (a plain audio file, a stream), and in that case the profile picture
is still what appears, so it should still obey the setting.

**Keep the expression and the variable in sync.** If a future edit adds an art source to
`Image_ButtonMenu_NowPlayingThumb`, add the same test here or that artwork will be hidden.

No reload is needed after toggling — `<visible>` conditions are polled, and `Skin.HasSetting`
updates live.

### Layout consequence: none
Hiding the icon does not shift the labels and does not leave a gap. In `_Dialog_Special_Button` the
two labels are `left 40` / `right 100` and **left-aligned** (no `<align>`, so Kodi's default). The
`right 100` only reserves space; the text starts at the left edge either way. The tile simply loses
its circle.

### String
`#31603` "Show profile picture" appended to all 13 `language/resource.language.*/strings.po`
files with an empty `msgstr` (same convention as `#31598`/`#31599`). `#33063` ("Options") is a Kodi
core string already used as the dialog 1170 header, reused here for the prefix.

### Deliberately not done
* **No gating on whether a `NowPlaying` tile is actually configured.** The setting is always
  visible in Layout → Other. Gating would mean testing all four `OptionsTiles.NN.Include` strings
  in the entry's `<visible>`, which is noise for a setting that is inert when unused.
* **The setting is not mirrored into the Options-tile configuration dialog**
  (`Items_Settings_PowerMenuConfigure`). One home for it, per the request.
* `slevel` was left at the default `0`, so the entry shows at Basic settings level. The
library-section toggle added in section 13 sits directly beneath this one (id `011`) and follows
the same pattern.

## 13. Library section breadcrumb hidden in the viewline

The "viewline" is the small line of text above the list in every media view — `Info_Viewline`
in `1080i/Includes_Info.xml`. Its default label was the current section breadcrumb: **"Titles"**
in Movies, **"Artists"** in Music, and so on. That breadcrumb is now suppressed **while browsing
the local library only**.

### What was actually being rendered
`Info_Viewline`'s `label` param defaulted to:

```
$VAR[Label_Section_Main]$VAR[Label_Section_SortMethod, [COLOR=main_fg_30]&gt;[/COLOR] ,]
```

`Label_Section_Main` (`1080i/Includes_Labels.xml`) is a long fallback chain; in the library the
match is normally one of the *Content Labels* (`Container.Content(albums)` → "Albums",
`(songs)` → "Songs", `(seasons)` → "Seasons") or the *Category Label*
`$INFO[Container.FolderName]`, which is what produces "Titles" and "Artists" — those are the node
names Kodi reports for `videodb://movies/titles/` and `musicdb://artists/`.

`Label_Section_SortMethod` is a *different* thing and was deliberately kept: it only resolves when
the sort / filter / alphabet controls have focus, and it is functional feedback ("Sorted by: Title",
"Filtered > …"), not a section name.

### Implementation
Three small pieces, no deletions:

1. **`1080i/Includes_Expressions.xml`** — the gate:

```xml
<expression name="Exp_Viewline_HideLibrarySection">[String.IsEmpty(Container.PluginName) + [String.StartsWith(Container.FolderPath,videodb://) | String.StartsWith(Container.FolderPath,musicdb://) | String.StartsWith(Container.FolderPath,library://)]]</expression>
```

2. **`1080i/Includes_Labels.xml`** — a wrapper variable placed immediately after
   `Label_Section_Main`:

```xml
<variable name="Label_Section_Viewline">
    <value condition="$EXP[Exp_Viewline_HideLibrarySection]">$VAR[Label_Section_SortMethod]</value>
    <value>$VAR[Label_Section_Main]$VAR[Label_Section_SortMethod, [COLOR=main_fg_30]&gt;[/COLOR] ,]</value>
</variable>
```

3. **`1080i/Includes_Info.xml`** — `Info_Viewline`'s `label` **default** now reads
   `$VAR[Label_Section_Viewline]`. The stock string is preserved verbatim in a comment above it.

### User setting
**Setting key:** `Skin.HasSetting(Furniture.ShowLibrarySection)` — **default off (unset)**, i.e.
the breadcrumb is hidden out of the box.
**UI location:** Skin Settings → **Layout** → **Other**, directly below the profile-picture toggle
from section 12, labelled `$LOCALIZE[14022] > $LOCALIZE[31604]` → "Library > Show section name".
Control id `4011`. New string `#31604` "Show section name" appended to all 13
`language/resource.language.*/strings.po` files.

The setting is a *positive* opt-in, so it is the leading negated term of the gate rather than a
separate wrapper condition:

```xml
[!Skin.HasSetting(Furniture.ShowLibrarySection) + String.IsEmpty(Container.PluginName) + [ ...paths... ]]
```

The `Furniture.` namespace was chosen to match `Furniture.ViewLineColour`, the skin's existing
setting for this same line. No reload is needed after toggling — `<visible>` and label conditions
are polled live.

**To revert the whole feature** (removing the setting from the equation): set
`Exp_Viewline_HideLibrarySection` to `[False]`. Nothing else needs touching; the settings entry
becomes inert rather than broken.

### Why a wrapper variable and not an empty `<value>`
The obvious edit — adding `<value condition="...">` with empty content to `Label_Section_Main` —
relies on Kodi accepting a `<value>` node with no text child and returning an empty string for it.
That is not reliable across versions. Returning `$VAR[Label_Section_SortMethod]` instead is
guaranteed-safe: it is a real info label that simply resolves to nothing in the common case,
**and** it is exactly the piece that should survive.

### Why only the param *default* was changed
`Info_Viewline` has ~20 call sites. Most pass their own `label`:
widget row titles (`Includes_Widgets.xml`), the OSD panels, cast lists,
`Custom_1142_OSD_MusicTracks.xml` (hardcoded "Albums"), and every
`View_Furniture_Bottom_ViewLine` caller (page counters, `View_Scrollbar_Count`). Redirecting only
the default leaves all of those untouched — no need to audit them.

The call sites that *do* use the default are the library views themselves
(`Includes_Views.xml`, 6 sites), `MyFavourites.xml`, `MyPlaylist.xml`,
`Includes_Views_PVR.xml` and `DialogPVRChannelGuide.xml`. The last four are unaffected in practice
because their container paths are `favourites://` / `pvr://` / empty, so the expression is false
and they keep the stock label.

### Scope — what is and is not hidden
With the setting off (the default), hidden: everything under `videodb://`, `musicdb://` and `library://`. That includes movies, TV
shows, seasons, episodes, music videos, artists, albums, songs, and — worth knowing — **also the
more specific breadcrumbs**: a genre name, a set name, a show title, and the skin's own
`MediaFilter.BaseDir.Name`. If only the generic node words ("Titles"/"Artists") were meant to go
while genre and set names stayed, that is a different and much more fragile change: the two come
from the same `Container.FolderName` info label, so they can only be told apart by inspecting the
path shape, not by which variable value matched.

Not hidden: plugins (`plugin://`, and `Container.PluginName` non-empty), Files and Sources views,
Add-on browser, PVR, Favourites, playlist windows, and the video/music **Playlists** directories
(`special://videoplaylists/` etc., which still show their own folder name).

### Cosmetic side effect
Where `Info_Viewline` is called with `use_spinner=true` (the library views), the busy spinner lives
in a horizontal grouplist *after* the label. With the label empty the spinner shifts to the left
edge of the viewline instead of trailing the text. It is still visible and still animates.

---

## 14. Kodi-library prebuilt layout removed; TMDb Helper layout is now the install default

The skin shipped two prebuilt menu/widget presets under `shortcuts/prebuilt/`: `library-basic`
(built on `library://` nodes) and `tmdb-basic` (built on `plugin.video.themoviedb.helper`).
The wizard asked which one to install. **`library-basic` is gone and `tmdb-basic` is applied
unconditionally.**

### What was removed

* `shortcuts/prebuilt/library-basic/` — deleted (`skinvariables-shortcut-homemenu.json`,
  `skinvariables-shortcut-sidemenu.json`).
* The `run_dialog=select` chooser (`list=library-basic||tmdb-basic`) at both of its call sites.

### The two call sites, and why they ended up different

There were **two** places running that chooser. Only one was the wizard; missing the other leaves a
dead menu item pointing at a deleted folder.

1. **`1080i/Custom_1180_Wizard.xml`, hidden button 8002** — the wizard finish handler from §5.
   The select call is replaced with a direct, silent apply. No prompt: the wizard has nothing left
   to ask.
2. **`1080i/Custom_1115_Dialog_Shortcuts_Window.xml`, button id 9001 ("Prebuilt")** — the manual
   re-apply in the shortcut editor. This one **keeps a confirmation**, as `run_dialog=yesno`.
   The select dialog was previously doing double duty as the cancel path, and this action
   **overwrites the user's existing menu** — collapsing it to a one-click destructive button would
   have been a regression. Reuses existing strings only: heading `31102`, message `31079`,
   yeslabel `31456`, nolabel `222` (Cancel). No new strings, no `.po` edits.

### Single source of truth for the path

`shortcuts/builtins/skinvariables-prebuiltwidgets.json` was an **orphan** in the upstream skin —
present, correct, referenced by nothing. It is now the one definition of the copy action, with the
`{v}` placeholder folded to the literal folder:

```json
{
    "actions": [
        "route=copy_menufolder=special://skin/shortcuts/prebuilt/tmdb-basic/&skin={skin}"
    ]
}
```

Both call sites invoke it through the skin's established `run_executebuiltin` pattern, so the
prebuilt folder name lives in exactly one file. `{skin}` must still be passed as
`$VAR[Path_SkinVariables_SkinFolder]` — that variable resolves to `skin.arctic.vibe` **or**
`skin.arctic.vibe-<user>` when a skin user is set (`Includes_Paths.xml`), so hardcoding it would
break multi-user menu configs.

### Shipped defaults changed — this is what actually makes it "first install"

The root `shortcuts/skinvariables-shortcut-homemenu.json` and `-sidemenu.json` are the defaults
`script.skinvariables` seeds into `addon_data` when no user config exists. **They were the library
layout** (the root homemenu was `library-basic` with one entry reordered; the root sidemenu was
byte-identical to `library-basic`'s). Both are now copies of the `tmdb-basic` files.

> **Correction (see §15).** Only `-sidemenu.json` matters. The Home window's menu is
> `menu=sidemenu`; there is no `menu=homemenu` anywhere in the skin, so
> `skinvariables-shortcut-homemenu.json` is inert. Changing both was harmless, but the
> file that made the difference is the sidemenu one.

Without this, the first-run sequence would still have flashed the library menu: the shortcut
rebuild fires on the wizard's Dialog step, *before* the reload and before 8002 does the copy. If
the copy ever failed, the user would have been left on exactly the layout being removed. With the
defaults changed, the TMDb layout is correct from the first build and the 8002 copy is belt-and-braces.

Prior mods had **not** touched these two files (`SKINMOD` appears nowhere in `shortcuts/` outside
`generator/`), so overwriting them reverted nothing.

### Coupled behaviour worth knowing — interacts with §8c

§8c changed the default widget viewtype to Poster and noted the shipped presets mostly override it
by naming Landscape explicitly. Swapping the defaults changes those counts: homemenu **6 → 2**
explicit Landscape declarations, sidemenu **10 → 2**. The Poster default from §8c is therefore
visible on considerably more rows out of the box than that section describes.

### Left deliberately in place

* String `31395` ("Choose prebuilt widget configuration") is now unreferenced. Left in all 14
  `.po` files — inert, and editing 14 translation catalogues to delete one msgid risks more than
  it gains.
* Strings `31078` / `31456` / `31102` still read as though several presets exist ("various
  different default widget and menu sets"). Slightly inaccurate now; changing them means editing
  every `.po`. Not done.
* `spotlight_*` keys inside `prebuilt/tmdb-basic/*.json` — inert, same reasoning as §1.

### Validation

XML well-formedness (208 files), JSON validity (23 files), zero dangling includes, and unresolved
variable/expression count **identical to the original archive** — no regression. (Note: the
enumeration used here reports 35 rather than the 34 quoted above; the extra name is
`Exp_View_` from the same dynamic-construction family, and the figure is 35 both before and after
this change. What matters is that it did not move.)

---

## 15. First-run sidemenu flash removed; broken Trakt widget paths fixed

Two defects found from a `kodi.log` of a clean install.

### 15a. The vertical sidemenu flash before the wizard

**Symptom:** a layout with a vertical left-hand sidemenu appears for ~1 second immediately before
the setup wizard opens.

**Cause — `1080i/Home.xml`, first-run block.** Upstream forced Basic mode on the very first Home
load:

```xml
<onload condition="!Skin.HasSetting(Home.FirstRun)">Skin.SetBool(Hub.Home.DisableSubmenu)</onload>
```

`Hub.Home.DisableSubmenu` is the whole switch. In `Hub_Controls` (`Includes_Hubs.xml`) it feeds
`is_homemenu`, and that one param picks the layout:

| `is_homemenu` | Menu rendered | Where the items come from |
|---|---|---|
| **true** (Basic) | `Home_Menu` → `Home_Menu_Vert` → list 300, **vertical** | `skinvariables-sidemenu-staticitems` |
| **false** (Advanced) | `Hub_Categories`, `<orientation>horizontal</orientation>` | the same `sidemenu` items |

So the vertical sidemenu *is* Basic mode — the layout §5 removed from the UI but which Home.xml was
still switching on at first run. The wizard's Dialog step then ran `Skin.Reset(Hub.Home.DisableSubmenu)`
and reloaded, which is why it only flashed.

**Fix.** `Skin.SetBool` → `Skin.Reset`, plus an explicit reset of `EnableDetailedInformation`, so
first run lands on Advanced directly. Reset rather than simply deleting the line: skin settings live
in `userdata/addon_data/skin.arctic.vibe/settings.xml` and **survive a reinstall**, so a
`DisableSubmenu` stored by an earlier install would otherwise persist.

The other three first-run onloads (`Shortcuts.RebuildDateTime`, `Home.FirstRun`, `ReloadSkin`) are
untouched — they build the generated includes, which do not exist on the very first load.

### The trap here — do NOT delete the sidemenu shortcut config

The obvious reading is "the sidemenu is gone, so delete `skinvariables-shortcut-sidemenu.json` and
the sidemenu copy inside `prebuilt/tmdb-basic/`." **That would empty the home menu completely.**

`sidemenu` is the *name of the menu data*, not the name of the vertical layout. `Home.xml` passes
`categories=sidemenu`, and `Includes_Shortcuts_Window.xml` resolves the Home entry of the shortcut
editor to `menu=sidemenu`. Every generator stage that builds the home menu reads `menu="sidemenu"`
(`home_classic.xml`, `home_widgets.xml`, `home_listcheck.xml`, `side_items.xml`, `side_vars.xml`,
`hubs_complex.xml`). Advanced mode renders exactly the same items — just horizontally. The file is
the home menu.

Related finding: **`skinvariables-shortcut-homemenu.json` is the one that is actually inert.**
Grepping every `menu=` token in the skin yields `sidemenu`, `1101menu`–`1109menu`, `options-tray`,
`powermainmenu`, `powersidemenu`, `searchmenu` — no `homemenu`. Note the include named
`skinvariables-homemenu-widgets` is generated *from* `menu="sidemenu"` (see `home_widgets.xml`), so
its name is misleading. The dead `-homemenu.json` files (root and `prebuilt/tmdb-basic/`) are left
in place: they are harmless, and `script.skinvariables` seeding behaviour was not verifiable from
the skin source alone.

### 15b. The error notification after the wizard

**Symptom:** a notification flashes too fast to read once the wizard finishes.

**What the log shows.** At the moment described, the only errors are two failing widget rows:

```
error <general>: XFILE::CDirectory::GetDirectory - Error getting
  plugin://plugin.video.themoviedb.helper/?list_name=Best+Movies+Last+Decade
  &...&user_slug=jurialmunkey&...&owner=true&...&info=trakt_userlist&...&reload=&reload=
```

…and the same for `Highly+Rated+TV+Shows`. Both are Trakt lists owned by jurialmunkey, shipped in
the `tmdb-basic` preset.

**Why those two and not the others.** The preset contains four `trakt_userlist` widgets. The two
IMDb ones (`user_slug=justin`) load in the same pass and produce **no error** — public Trakt user
lists resolve without the user being signed in. The only difference between the pairs is
**`&owner=true`**, which tells TMDb Helper to resolve the list against the *authenticated* Trakt
account. On a fresh install there is no Trakt account, and the lookup fails. On an account that
*is* signed in it would still be wrong — the lists belong to someone else.

**Fix.** `&owner=true` stripped from both paths. Also collapsed a duplicated
`&reload=$INFO[Window(Home).Property(TMDbHelper.Widgets.Reload)]` that appeared **twice** on all six
TMDb Helper widget paths (visible in the log as the trailing `&reload=&reload=`; the property is
empty at first build). Applied to both `shortcuts/skinvariables-shortcut-sidemenu.json` and
`shortcuts/prebuilt/tmdb-basic/skinvariables-shortcut-sidemenu.json` — **they must stay identical**,
since the wizard copies the prebuilt over the seeded default. Verified byte-for-byte identical after
the edit.

**Confidence.** Kodi does not log the text of notification dialogs, so the popup cannot be read back
from the log directly. What can be said: these two errors are the only failures logged in that
window, and they land 2.4s after the post-wizard skin reload. `owner=true` is a plain defect
regardless.

**Not the cause, but present in the log:** `Skin has invalid include: skinvariables-options-tray-onload`
(≈9 per skin load, every load). `options_tray.xml` / `options_onload.xml` read `menu="options-tray"`,
and no `skinvariables-shortcut-options-tray.json` ships — the options tray is user-populated, so the
generator emits nothing and the include never exists. Upstream behaviour, log-only, never surfaced
to the user. Left alone. Likewise the `skinvariables-sidemenu-*` invalid-include warnings and
`Control 300 in window 10000 has been asked to focus` at 11:31:02 — those are the *first* load,
before the generator has run; the first-run rebuild-and-reload is what fixes them.

### Still Trakt-dependent by design

All four `trakt_userlist` widgets in the preset remain other people's Trakt lists, and the two
`trakt_trending` rows are Trakt endpoints. They resolve unauthenticated today, but they are outside
the skin's control. Swapping them for TMDb-native equivalents (`info=popular`, `info=trending`,
`info=top_rated`) would remove the Trakt dependency from the shipped default entirely. Offered, not
done — it changes what a fresh install actually shows, which is a content decision rather than a bug fix.

---

## 16. Preset trimmed to Movies / TV Shows; hub labels made specific

### 16a. Home hub shortcuts and widgets removed

Removed from the home menu (`menu=sidemenu`), whole shortcut plus its widget rows:

| Shortcut | Widgets removed with it |
|---|---|
| Discover (`ActivateWindow(1105)`) | IMDb: Top Rated Movies, IMDb: Top Rated TV Shows |
| Add-ons (`addons://sources/video/`) | Video add-ons, Music add-ons |
| Live TV (`ActivateWindow(TVGuide)`) | TV Channels, Recordings |

Individual widgets removed, shortcut kept:

* **Movies** → Best Movies Last Decade
* **TV Shows** → Highly Rated TV Shows

What remains is two shortcuts, one widget each: Movies → Trending, TV Shows → Trending.

Applied to **both** `shortcuts/skinvariables-shortcut-sidemenu.json` (the seeded default) and
`shortcuts/prebuilt/tmdb-basic/skinvariables-shortcut-sidemenu.json` (what the wizard copies).
Per §14 these must stay byte-identical; verified after the edit.

The `expression` and `forced` keys on the deleted items (`forced: True` on Discover) are inert —
`expression` is written into the item layout by `additional_sidemenu.xmltemplate` but never read
back, and nothing in the skin consumes `forced`. Deleting the whole item is safe.

**Side effect that resolves §15b.** All four `trakt_userlist` widgets are now gone from the preset,
including the two that were erroring. The `owner=true` fix in §15b stays anyway — it is still
wrong, and the widgets remain addable from the shortcut editor's TMDb Helper grouping.

**Discover is not orphaned.** Window 1105 is still reachable: `Includes_Actions.xml` swaps the
sidemenu search button for Discover under `Skin.HasSetting(SideMenu.SwapSearchForDiscover)`, and it
remains in `skinvariables-shortcut-config.json` as an addable shortcut.

The inert `-homemenu.json` pair (see §15a) was trimmed to match so the two files do not contradict
each other. It still has no consumer.

### 16b. Hub labels: generic "Hub" → per-window names

**Where it shows:** Settings → Shortcuts → Customise shortcuts (window 1115). The hub list built
`$NUMBER[1101]$VAR[Label_Header_Main_1101, (,)]`, and every one of those variables fell back to
`$LOCALIZE[31026]` ("Hub"), so the list read `1101 (Hub)`, `1102 (Hub)`, and so on.

**Fix — `1080i/Includes_Labels.xml`.** Only the *fallback* `<value>` of each
`Label_Header_Main_<id>` changed; the `Skin.String(Hub.<id>.Label)` branch above it is untouched, so
a user-set hub name still wins.

| Hub | Window file | New default |
|---|---|---|
| 1101 | `Custom_1101_Hub_Movies.xml` | `$LOCALIZE[20342]` Movies |
| 1102 | `Custom_1102_Hub_TVShows.xml` | `$LOCALIZE[20343]` TV Shows |
| 1103 | `Custom_1103_Hub_Music.xml` | `$LOCALIZE[249]` Music |
| 1104 | `Custom_1104_Hub_PVR.xml` | `$LOCALIZE[31085]` Live TV |
| 1106–1109 | `Custom_110<6-9>_Hub_Custom<1-4>.xml` | `$LOCALIZE[636]` Custom |

All five string ids were already in use elsewhere in the skin, so no `.po` edits were needed.

**Wider than the one list — by design.** `Label_Header_Main_<id>` also feeds `Label_Header_Main` and
`Label_Header_Hubs_Categories`, i.e. the hub page header itself. An unconfigured hub 1101 used to
show "Hub" at the top of the page and now shows "Movies". That is the same generic label, so it was
left consistent rather than special-cased.

`Label_Shortcut_SubHeader_GroupName` (24 values) was also switched from `$LOCALIZE[31026]` to
`$VAR[Label_Header_Main_<id>]`. That is the breadcrumb one screen deeper in the same flow — it read
"1101 Hub Categories" while the list it was reached from now says "1101 (Movies)". The numeric id is
deliberately kept in front: it is the only thing distinguishing 1106–1109, which all default to
"Custom".

### Deliberately left as "Hub" — do not "finish the job" here

The hub **window selector** still reads `Hub 1101`. Two files hold it:

* `1080i/Custom_1115_Dialog_Shortcuts_Window.xml:192` — the `list=` of a `run_dialog=select`.
* `1080i/Includes_Shortcuts_Window.xml:84-93` — `Shortcuts_Window_Var_HomeItem_Property_Current_Home_Hub`,
  passed as `preselect=` to that same dialog.

**They are a matched pair.** The preselect works by string-matching the current value against a list
entry, so changing one without the other silently breaks preselection. And the list is `||`
separated, so substituting `$VAR[Label_Header_Main_<id>]` would let a user-set hub label containing
`|` corrupt the list. Both were left alone.

Also untouched: the `CustomShortcut.Item0<n>` labels in `Includes_Labels.xml` (~16 values, e.g.
"Movies Hub"). Those name a *shortcut to* a hub, where "Hub" is the meaningful noun.

---

## 17. Hubs screen button tray removed; dead shortcut configs deleted

### 17a. Prebuilt / Rebuild buttons

`1080i/Custom_1115_Dialog_Shortcuts_Window.xml` — the bottom button tray, **grouplist 305** in its
entirety (56 lines), containing:

* **9001 "Prebuilt"** (`$LOCALIZE[31456]`) — the yes/no confirm added in §14. Only one prebuilt
  layout exists and the wizard installs it on first run, so there is nothing to choose or restore.
* **9002 "Rebuild"** (`$LOCALIZE[31494]`) — `RunPlugin(...&func=do_refresh...)` followed by
  `ActivateWindow(1115)`.

**Why removing Rebuild is safe.** The rebuild is already automatic, via two independent hooks:

1. Window 1115 has `<onunload>Skin.SetString(Shortcuts.RebuildDateTime,<timestamp>)</onunload>`
   (line 18). The shortcut editor 1116 has the identical onunload. Leaving either screen stamps a
   new build time.
2. `Action_Hubs_Onload` (`Includes_Actions.xml:289`) includes `Action_BuildShortcuts_OnLoad`, which
   runs `shortcuts/skinvariables-build-templates.json` on **every** Home/hub window load. That file
   passes `lastbuildtime={skinstring_lastbuildtime}` — read from `Shortcuts.RebuildDateTime` — into
   `route=action=buildtemplate`, so a changed timestamp forces regeneration.

So: edit shortcuts → leave 1115 → land on Home → rebuild runs. The button only ever saved the trip
back to Home.

**The trap — three dangling nav targets.** Grouplist 3000 and two of its buttons pointed down into
the deleted tray:

* line 175 — `<ondown>305</ondown>` on grouplist 3000 itself
* lines 273, 298 — on buttons 398 ("Edit global widgets") and 395 ("Edit widgets")

Left in place these produce `Control 305 in window 1115 has been asked to focus, but it can't`
on every down-press at the bottom of the list — the same error class already visible in the install
log. All three were removed; down now simply stops at the last button, which is Kodi's default
grouplist behaviour.

Strings `31456`, `31494`, `31104`, `31080`, `31081` are now unreferenced. Left in the `.po` files,
per the §14 convention.

### 17b. Dead shortcut config files deleted

```
shortcuts/skinvariables-shortcut-homemenu.json
shortcuts/prebuilt/tmdb-basic/skinvariables-shortcut-homemenu.json
```

These are the files identified as inert in §15a: the home menu is `menu=sidemenu`, and there is no
`menu=homemenu` token anywhere in the skin. They were the last place still describing widgets that
no longer exist in the shipped config (`Random Movies Genre`, `Shut Up, And Watch`), which is
exactly why they looked like stale prebuilt configs.

Confirmed before deleting: zero references remain to `homemenu` outside the generator's own
`skinvariables-homemenu-widgets` / `is_homemenu` / `widget_homemenu` / `listcheck_homemenu` names —
none of which read these files. The include `skinvariables-homemenu-widgets` is generated from
`menu="sidemenu"` (`home_widgets.xml`), so it is unaffected.

### Kept deliberately — every other json under `shortcuts/`

Checked one by one; none is a stale prebuilt config:

| File | Why it stays |
|---|---|
| `skinvariables-shortcut-sidemenu.json` | **The home menu.** See the §15a warning. |
| `prebuilt/tmdb-basic/skinvariables-shortcut-sidemenu.json` | What the wizard copies on first run. |
| `skinvariables-shortcut-powermainmenu.json` | 3 live `menu=powermainmenu` references. |
| `skinvariables-shortcut-searchmenu.json` | 5 live `menu=searchmenu` references. |
| `skinvariables-shortcut-config.json` (46KB) | **Not a menu config** — the catalogue of shortcuts the editor offers when you add an item. It lists library nodes, add-ons, PVR and TMDb Helper paths *by design*; that breadth is the point. Deleting it empties the "add shortcut" browser. |
| `skinvariables-shortcut-context.json` | 58 bytes, all-empty (`mainmenu`/`widgets`/`basic`). Not a menu list and has no `menu=context` consumer, but its structure suggests `script.skinvariables` reads it by filename convention. Empty either way — removing it risks a missing-file path for no gain. |
| `builtins/skinvariables-prebuiltwidgets.json` | Still the wizard's copy action (§14). The 1115 button is gone; hidden button 8002 in the wizard is not. |
| `skinvariables-build-*.json`, `skinvariables-generator*.json`, `skinvariables-startup.json`, `skinviewtypes.json` | Build/generator/startup/viewtype machinery, unrelated to menus. |

`menu=powersidemenu` is referenced by the skin with no shipped json — that menu is user-created and
the file appears in `addon_data` only once populated. Not a missing file.

---

## 18. Combine Widgets no longer collides with the widget row

**Symptom.** Turning on **Combine Widgets** for a category (shortcut editor → *Category: Combine
Widgets*) made the category's submenu button strip overlap the first widget row.

**Cause.** Two separate positioning systems that section 3 pulled apart.

The button strip is grouplist **3002** (`Hub_Submenu_Group`, `1080i/Includes_Hubs.xml`), pinned at
`hub_submenuwidget_t` (1300) and nested inside `Hub_Menu_Group` — which carries the permanent
**-440** offset described in section 1. It therefore rests at **~860px** and has no relationship
to `hub_widgets_shift_y`. Section 3 moved widget wrapper group **340** down 140px; the strip stayed
where it was, and the row landed on top of it.

The strip only exists for combined categories, so this was invisible until Combine Widgets was
switched on. Its generated visibility condition
(`shortcuts/generator/data/base/category_submenu.xml`) is:

```
!String.IsEmpty(Container(300).ListItem.Property(use_as_widget))
```

**Fix.** New include `Hub_Slide_Widgets_OnCombined` (`1080i/Includes_Hubs.xml`), added to group 340
in `Hub_Standard`. It slides the wrapper back up by exactly `hub_widgets_shift_y` when the focused
category has Combine Widgets on, so **that category renders at the original upstream spacing** and
every other category keeps the mod's lowered rows.

```xml
<include name="Hub_Slide_Widgets_OnCombined">
    <param name="posy">-hub_widgets_shift_y</param>
    <param name="condition">[String.IsEmpty(Container(300).ListItem.Property(use_as_widget))]</param>
    <param name="time">1</param>
    ...
</include>
```

### Why an animation and not `<top>`
`use_as_widget` is a **per-category runtime property** — it changes as the user moves along the
categories list. `<top>` / `<bottom>` and `<include content="X" condition="...">` are both resolved
**once at skin load**, so neither can react to it. A conditional slide on the wrapper is the skin's
own idiom for this (`Hub_Slide_Group_OnWidgets`), and — like the `<top>` in section 3 — it
translates the entire block, so it **cannot alter row-to-row pitch**. The section 3 warning about
`widget_items_h` still stands and is not touched here.

### Inverted condition — read carefully before editing
Following `Hub_Slide_Group_OnWidgets`, `posy` is the resting offset applied when `condition` is
**FALSE**. So `condition` holds the ***un*-combined** case (`String.IsEmpty(...)`), where the group
rests at 0 and the section 3 shift stays intact. Negating the condition inverts the whole feature.

### Only `Hub_Standard` needs this
`Hub_Widgets_Only` also has a group 340, but it is the `Hub.<id>.DisableSubmenu` path, and
`category_submenu.xml` is gated on `!Skin.HasSetting(Hub.{window_id}.DisableSubmenu)`. The strip
can never render there, so its `_detailed` (580) shift is left alone.

### `time` defaults to 1 (instant)
A 400ms slide would leave a window where the row is mid-travel while the strip fades in — briefly
reproducing the collision the fix exists to remove. Raise it to 400 for a slide matching
`Hub_Slide_Group_OnWidgets` if the snap reads as abrupt.

### Not changed: fanart size
`flixart_size_h` (section 4) was raised 140px to match the row shift, but it is a **constant**, and
Kodi constants cannot vary per category. Combined categories therefore sit at stock row height
against a slightly taller fanart panel. This is cosmetic — the panel is top-right anchored with a
gradient falloff — and reverting it would need a second `Background_FlixArt` variant keyed on the
same condition. Not done; noted here in case the overlap is ever judged too heavy.

**No rebuild required.** The change is in `1080i/`, not under `shortcuts/generator/`.

---

## 19. "Hide widget initialisation behind splash" made to work in Advanced mode

**Symptom.** Skin Settings → Other → Startup → *Hide widget initialisation behind splash*
(`Skin.HasSetting(Startup.WaitForLoad)`, string `#31428`) had no observable effect. Kodi splash →
skin splash → Home appears immediately with empty widget rows that fill in over several seconds.

### What the setting actually does

It is **not** an extension of window 1198. It draws `Hub_Widget_Splash` as a full-screen overlay
*inside* the Home/hub window — `Background_StartUp` (which is what cycles the
`Skin.String(Startup.ImageFolder)` launch images), `Object_StartUp_Logo`, an "initialising" label and
a busy spinner. Because it reuses the same furniture, it reads as the skin splash lingering.

`Hub_Controls` (`1080i/Includes_Hubs.xml`) contains **three** copies of that include. Which one is
compiled is decided at **skin load** from two params:

| Variant | Compiled when | Watched container |
|---|---|---|
| 1 | `[$PARAM[is_homemenu]]` | `Container(100601)` — first widget row |
| 2 | `![is_homemenu]` + `![widgets_only]` + `!DisableForHubs` | **`Container(300)`** — category menu |
| 3 | `![is_homemenu]` + `$PARAM[widgets_only]` + `!DisableForHubs` | `Container(100601)` |

### The defect

Variant 2's condition was:

```
System.HasAlarm(SplashTimeOut) + Integer.IsEqual(Container(300).NumItems,0) + Container(300).IsUpdating
```

Container 300 is the category selector — a `fixedlist` fed by the generator's
`skinvariables-<menu>-staticitems` (`Categories_Selector` in `1080i/Includes_Categories.xml`). It is
**static**: populated the instant the window opens, and it never reports `IsUpdating`, because that
flag only goes true for background directory fetches. Both of the last two terms are permanently
false, so the overlay could never draw.

**And variant 2 is the one Home compiles.** `Home.xml` passes *both*
`is_homemenu` and `widgets_only` as `Skin.HasSetting(Hub.Home.DisableSubmenu)`, which §15b resets so
that first run lands on Advanced — so both are false. Every hub with a submenu compiles it too. The
setting therefore worked **only** on a `widgets_only` hub (variant 3).

This is an upstream hole — Advanced-mode users never had the feature in AF2 either. Forcing Advanced
(§5, §15b) is what made it universal.

Not the cause: the `SplashTimeOut` alarm. `Action_Hubs_Onload` restarts it on every Home/hub load
(visible in a debug log as `started alarm with name: splashtimeout`), so it is always live.

### Fix

Variant 2 now watches the **first widget row of the first category**, using the same empty-or-
placeholder + `IsUpdating` test the other two variants use.

Getting the right container id is the whole difficulty, because there is no single widget id space.
`setup/widgets_row.xml` assigns `widget_id = grouplist_item_x * 1000 + 600 + enum_x` (`enum_x`
1-based), but `grouplist_item_x` comes from whichever datafile the path pulls in:

| Path | Datafile | `grouplist_item_x` | First row |
|---|---|---|---|
| Classic / `widgets_only` hub | `setup/widgets_hubsmenu.xml` | `100` (constant) | 100601 |
| Classic Home (sidemenu) | `base/home_widgets.xml` | `item_x + 100` | 100601 |
| Global widgets | `setup/widgets_constant.xml` | `200` (constant) | 200601 |
| **Advanced (this variant)** | `setup/widgets_standard.xml` | `{item_x}` — **no offset** | **601** |

Advanced Home and Advanced hubs reach `Hub_Standard` → `skinvariables-<categories>-widgets` →
`base/category_widgets.xml` → `setup/widgets_standard.xml`. So **100601 does not exist in Advanced
mode at all**. That is exactly why `Includes_Actions.xml` only ever references 100601 from
`Action_Hubs_Classic_SetFocus` (line ~318) and from the `onload` gated on
`is_homemenu | DisableSubmenu` (line ~272) — upstream never assumes it on the Complex path.

`item_x` is 0-based: `home_widgets.xml` uses `item_x + 100` for the same role that
`widgets_hubsmenu.xml` fills with the constant `100`, so the two classic paths only share the 100601
id space if the first item is 0. That gives **601**.

**Do not add an OR arm on another category's row as a safety net.** A `CDirectoryProvider` that has
never fetched reports `IsUpdating` **true** indefinitely — it is not "idle", it is "not yet run". Only
the focused category's rows fetch on window init (confirmed in debug logs: four providers refresh on
Home and nothing else ever does, even after navigating). So a second arm on, say, `Container(1601)`
sits permanently at `NumItems==0 + IsUpdating`, which latches the splash on forever.

A wrong id fails *safe*: a control that does not exist reports `NumItems` 0 and `IsUpdating` false, so
the splash simply never appears. A plausible-but-unfetched id fails *stuck*. Only single-arm
conditions on the focused category's first row are correct here.

The stock condition is preserved verbatim in the comment above the edit, along with the full id
derivation.

**To confirm the id directly**, read the generated
`1080i/script-skinvariables-generator-includes-<skinuser>.xml` on the device and find the first
`<control type="list" id="...">` under the `Hub_Menu_Group` include. That number is the first widget
row of the first category and is what this condition must name.

**No rebuild required.** The change is in `1080i/`, not under `shortcuts/generator/`.

### Correction history

**1.0.1** used `Container(100601)` for variant 2 and did nothing. The id was derived from
`base/side_vars.xml`/`base/home_widgets.xml`, both of which carry the `+ 100` offset, without checking
which datafile the Advanced path actually loads — it is `setup/widgets_standard.xml`, which has no
offset. Recorded because the four-way split above is not obvious from the templates and is easy to
get wrong the same way twice.

**1.0.2** used `Container(601) | Container(1601)`, the second arm hedging the 0-vs-1-based reading of
`item_x`. Result: the splash never cleared — it held past 30s and only dropped on a remote keypress.
That behaviour identifies the mechanism above (never-fetched provider ⇒ `IsUpdating` true) and, in
doing so, confirms `item_x` is 0-based: under the 1-based reading 1601 would have been a focused-
category row, fetched at startup, and the splash would have cleared on its own at ~8s.

**1.0.3** drops the hedge. Single arm on `Container(601)`.

### Two user settings this still depends on

* **`Startup.WaitForLoad.DisableForHubs` must stay off** (the "- Hubs" radio, string `#31114`,
  shown as *selected* when the setting is unset). Because Home now evaluates as a non-homemenu
  window, this sub-toggle silently governs **Home** as well as the hubs. With it on, variant 2 is not
  compiled at all and the fix is inert.
* **`Startup.ImageFolder` must point at a folder**, or the overlay shows the wordmark and spinner
  with no cycling launch images.

### Scope — what this does and does not hide

The overlay clears when the **first** widget row reports items, not when all of them finish. On a
measured cold start (Home window init 12:21:19.607, first row populated 12:21:29.223) that is ~9.6s
of splash, after which Home appears with row 1 filled and later rows still arriving. It hides the
worst of the wait, not all of it.

Widget rows on Home are slow mainly because Home is the window that loads *during add-on startup*:
the same TMDb Helper widget takes ~1.3s alone in a hub, ~2.8–3.7s in a quiet hub alongside four
others, and ~9–10s on Home while nine add-on services boot. Kodi also serves directory fetches from
**three** job worker threads, so a fourth and fifth provider queue — including cheap native ones. A
`library://music/recentlyplayedalbums.xml/` row measured a **6 ms** query that waited 9.4–11.9s for a
slot behind a plugin fetch. None of that is skin-side; it is noted here because it is what the splash
is covering up.

### Deliberately not changed

* **The `Hub.Home.ReplaceWindow` variants in `Home.xml`** (hub 1101–1109 rendered *as* Home) pass no
  `is_homemenu`, so they inherit `false` and also compile variant 2 — they are fixed by the same
  edit, but they remain gated on `DisableForHubs`. Arguably correct: the window really is a hub.
* **Variant 1 was left gated on `is_homemenu`.** It is unreachable while Advanced is the only mode,
  but it is stock, harmless, and the honest record of what the Basic path did. Decoupling Home from
  the `DisableForHubs` sub-toggle would need a new `is_home` param on `Hub_Controls` set from
  `Home.xml`; offered, not done.

### Version bumped to 1.0.3

`addon.xml` `version` `1.0` → `1.0.1` → `1.0.2` → **`1.0.3`**. See *Correction history* above for what
changed at each step.

Kodi's version comparison is per-component and numeric, so `1.0.3` > `1.0.2` > `1.0.1` > `1.0` and an install
over the top is treated as an upgrade rather than a reinstall.

**Why not `1.01`.** Because the comparison is numeric per component, `1.01` and `1.1` parse to the
same `[1, 1]` and compare **equal** — a later `1.1` release could not be installed over a `1.01` one.
Three-component patch numbering avoids that trap entirely. For the same reason `1.01` sorts *above*
`1.0.1` (`[1,1]` vs `[1,0,1]`), so if a `1.01` build was ever installed, Kodi will see this one as a
downgrade and refuse it — uninstall first, or bump past it.

Per §10, the bump is also what forces Kodi to re-read cached addon metadata; skin XML under `1080i/`
is re-read on every skin load regardless, so the bump is not what makes this fix take effect — but it
does mean the addon browser and the settings version line update. The version line needs no edit: it
reads live via `System.AddonVersion(skin.arctic.vibe)` and now renders "AV v1.0.3" (§11).

---

## Validation performed after every change

1. **XML well-formedness** across all files in `1080i/` (258 files at last count).
2. **No dangling include references** — every `<include content="X">` / `<include>X</include>`
   resolves to a defined `<include name="X">`, excluding runtime-substituted (`$PARAM`/`$VAR`)
   and generated (`skinvariables-*`) names.
3. **No dangling variable / expression references** — unresolved count held at the
   **pre-existing baseline of 34**. Those 34 are upstream dynamic name constructions
   (`Exp_View_<id>`, `Exp_View_<id>_Include`, `Color_PC_to_Hex_`, `View_`, `Label_Title_C`,
   `Label_Plot_C`, `Exp_1105Hub_IsVisible`, `Exp_IsPersonInfo`) and are **not** errors.
   Any number above 34 means something was broken.
4. **JSON validity** for all files under `shortcuts/`.
5. **Zip integrity** plus a file-manifest diff against the original archive to confirm nothing was
   dropped unintentionally (only `.git*` / `.github` CI metadata is excluded from the package).

---

## Quick reference — the tuning knobs

| What | Constant / file | Current |
|---|---|---|
| Hub widget row vertical position | `hub_widgets_shift_y` (+ `_detailed`) | 140 / 580 |
| Combine Widgets un-shift | `Hub_Slide_Widgets_OnCombined` (`time` param) | `-hub_widgets_shift_y`, instant |
| Row view vertical position | `view_row_shifted`, `view_row_hitrect_y_shifted` | 650 / 726 |
| Fanart panel size | `flixart_size_w` / `_h` (base = Medium) | 1689 x 950 |
| Wall grid geometry | `View_Wall_Include` `<top>` / `<bottom>` | 200 / 140 |
| Default widget view | `widgets_row.xml` fallback rule | `List_Poster_Row` |

10.8px = 1% of screen height. Remember to update `-name` negative twins.
